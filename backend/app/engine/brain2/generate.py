"""The demand-generation run — ``drafting -> compliance_review``, streamed over SSE.

This module composes Brain-2 into the ``drafting`` background run: it takes the matter's latest
APPROVED :class:`~app.models.orm.StrategyPlan`, generates the strategy memo, drafts + validates +
renders each planned section, and — when every section passes — advances the gate
(``DRAFTING -> COMPLIANCE_REVIEW``). Discipline mirrors :mod:`app.engine.brain1.analysis`: one SSE
frame per step (``format_sse`` + :class:`~app.models.enums.SseEvent` only), a per-matter run log
(invariant 14; phase ``"demand"``), typed refusals as ERROR frames, and never a raw traceback.

The two disciplines that ARE this run:

* **Surface, don't loop (inv 1, 13).** A section that fails deterministic validation gets ONE
  content retry (the violations appended to the prompt tail); if it still fails it is marked
  ``SURFACED_FAILED`` and an ERROR frame carries ``{section_id, violations}`` — and the run
  CONTINUES (the remaining sections still draft, so the attorney sees the whole picture, not just
  the first break). A run that ends with any surfaced-failed section does NOT advance the gate; the
  draft stays ``DRAFTING``.

* **Bind to the plan's version (inv, registry).** The draft binds to the plan's registry version; a
  ``plan.registry_version != matter.registry_version`` mismatch is a typed ``registry_drift`` ERROR
  frame and an early return (a bump invalidated the plan — the matter must re-confirm evidence
  first). No approved plan is a typed ``no_approved_plan`` ERROR frame (approval is a later wave's
  wiring; tests approve directly).

Re-entrancy: a ``DemandDraft`` is versioned (a resume after a budget stop is a NEW draft version,
never an overwrite). Metered budget errors end the run with an ERROR frame and stop — resuming is a
fresh run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sse_utils import format_sse
from app.core.audit import record_event
from app.core.llm_provider import LLMProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.core.matter_logs import MatterRunLogger
from app.core.tenancy import tenant_add
from app.engine.brain2.constraints import HardConstraintInputs, build_hard_constraints
from app.engine.brain2.drafter import draft_section
from app.engine.brain2.memo import generate_memo
from app.engine.brain2.renderer import render_section
from app.engine.brain2.validator import validate_section
from app.engine.orchestrator.machine import advance
from app.models.enums import DraftStatus, GateEvent, GateState, SectionValidation, SseEvent
from app.models.orm import DemandDraft, Matter, StrategyPlan, User
from app.models.schemas import PlannedSection

_LOG = logging.getLogger("clarionpi.brain2.generate")

# The run-log phase name (one file per matter/phase — invariant 14).
_PHASE = "demand"

# The required gate state to run; the route/caller enforces too.
_REQUIRED_GATE = GateState.DRAFTING.value

# Audit event kind on a completed (gate-advancing) run.
_COMPLETED_AUDIT_KIND = "draft_completed"


@dataclass(frozen=True)
class _SectionOutcome:
    """The result of drafting one section — its validation state + any surfaced violations."""

    section_id: str
    validation: SectionValidation
    violations: tuple[str, ...]


def _latest_approved_plan(db: Session, *, matter: Matter) -> StrategyPlan | None:
    """The matter's highest-version APPROVED :class:`StrategyPlan`, or ``None``.

    Approval is a later wave's wiring (the G2.5-approve side effect); tests set ``approved=True``
    directly. Highest version wins so a re-emitted-then-approved plan supersedes an older approval.
    """
    plans = list(
        db.execute(
            select(StrategyPlan).where(
                StrategyPlan.matter_id == matter.id,
                StrategyPlan.approved.is_(True),
            )
        ).scalars()
    )
    if not plans:
        return None
    return max(plans, key=lambda p: p.version)


def _next_draft_version(db: Session, *, matter: Matter) -> int:
    """One past the count of existing drafts for the matter (a draft version is never recycled)."""
    existing = list(
        db.execute(select(DemandDraft.id).where(DemandDraft.matter_id == matter.id)).scalars()
    )
    return len(existing) + 1


def _planned_sections(plan: StrategyPlan) -> list[PlannedSection]:
    """The plan's sections as validated :class:`PlannedSection`s in skeleton (list) order."""
    return [PlannedSection.model_validate(s) for s in plan.sections]


def run_demand_generation(
    db: Session,
    *,
    matter: Matter,
    user: User,
    provider: LLMProvider,
    run_logger: MatterRunLogger | None = None,
    post_draft: Callable[[Session, Matter, DemandDraft], None] | None = None,
) -> Iterator[str]:
    """Run the demand generation for ``matter``, yielding SSE frames (strings from ``format_sse``).

    Requires ``matter.gate_state == "drafting"`` (the caller/route enforces too — a wrong state is
    a typed ``wrong_gate_state`` ERROR frame + return). Requires a latest APPROVED plan whose
    ``registry_version`` matches the matter's (else a typed ``no_approved_plan`` or
    ``registry_drift`` ERROR frame + an early return).

    Per planned section (skeleton order): draft -> validate -> on violations, ONE retry (violations
    appended) -> re-validate -> still bad: ``SURFACED_FAILED`` + an ERROR frame ``{section_id,
    violations}`` (run CONTINUES); pass: ``PASSED``, render, and a ``section`` SSE
    ``{section_id, rendered_preview}``. A metered budget error ends the run with an ERROR frame and
    stops.

    After all sections: all PASSED -> ``draft.status = VALIDATED``; the ``post_draft`` hook runs
    (the compliance wave injects its G3 pre-check; ``None`` here); the gate advances
    (``DRAFTING -> COMPLIANCE_REVIEW``), a ``draft_completed`` audit fires, and a GATE_READY frame
    ``{gate: "compliance_review"}`` is emitted. Any SURFACED_FAILED -> the draft stays ``DRAFTING``,
    no advance, and a STATUS ``{state: "draft_incomplete", failed_sections: [...]}`` frame. A final
    STATUS ``completed`` frame carries the summary counts. Never raises through the stream.
    """
    logger = run_logger if run_logger is not None else MatterRunLogger(matter.id, _PHASE)
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)

    logger.log("run_started", gate_state=matter.gate_state)
    yield format_sse(
        SseEvent.STATUS,
        {"phase": _PHASE, "state": "started", "matter_id": str(matter.id)},
    )

    # ---- Preconditions: gate state, an approved plan, registry-version match --------------
    if matter.gate_state != _REQUIRED_GATE:
        logger.log("refused", reason="wrong_gate_state", gate_state=matter.gate_state)
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": _PHASE,
                "error": "wrong_gate_state",
                "detail": f"demand generation requires gate_state {_REQUIRED_GATE!r}, "
                f"matter is {matter.gate_state!r}",
            },
        )
        return

    plan = _latest_approved_plan(db, matter=matter)
    if plan is None:
        logger.log("refused", reason="no_approved_plan")
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": _PHASE,
                "error": "no_approved_plan",
                "detail": "no approved StrategyPlan for the matter (approve at G2.5 first)",
            },
        )
        return

    if plan.registry_version != matter.registry_version:
        logger.log(
            "refused",
            reason="registry_drift",
            plan_registry_version=plan.registry_version,
            matter_registry_version=matter.registry_version,
        )
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": _PHASE,
                "error": "registry_drift",
                "detail": f"plan registry_version {plan.registry_version} != matter "
                f"registry_version {matter.registry_version}; re-confirm evidence",
            },
        )
        return

    # ---- Create the draft row (versioned; a resume is a NEW version) ----------------------
    draft = DemandDraft(
        matter_id=matter.id,
        version=_next_draft_version(db, matter=matter),
        registry_version=plan.registry_version,
        strategy_plan_version=plan.version,
        status=DraftStatus.DRAFTING.value,
    )
    tenant_add(db, draft, matter.firm_id)
    db.commit()
    logger.log("draft_created", version=draft.version, plan_version=plan.version)

    # ---- Memo (attorney-visible artifact; degrades to "" visibly — it swallows its own
    # provider/budget errors internally, so no budget-stop handling is needed here) ---------
    yield format_sse(SseEvent.STATUS, {"phase": _PHASE, "state": "step", "step": "memo"})
    memo = generate_memo(db, client, matter=matter, plan=plan)
    draft.memo = memo
    db.add(draft)
    db.commit()
    logger.log("memo_generated", chars=len(memo))

    # ---- Per-section draft -> validate -> (retry) -> render -------------------------------
    constraints = build_hard_constraints(db, matter=matter)
    sections = _planned_sections(plan)
    outcomes: list[_SectionOutcome] = []

    for sort_order, planned in enumerate(sections):
        try:
            outcome = yield from _draft_one_section(
                db,
                client,
                matter=matter,
                plan=plan,
                draft=draft,
                planned=planned,
                constraints=constraints,
                sort_order=sort_order,
                logger=logger,
            )
        except BudgetExceededError as exc:
            logger.log("budget_stopped", section_id=planned.section_id)
            yield format_sse(
                SseEvent.ERROR,
                {
                    "phase": _PHASE,
                    "error": "budget_exceeded",
                    "detail": str(exc),
                    "section_id": planned.section_id,
                },
            )
            return
        outcomes.append(outcome)

    # ---- Finalize: advance iff every section PASSED ---------------------------------------
    failed = [o.section_id for o in outcomes if o.validation is SectionValidation.SURFACED_FAILED]
    passed = sum(1 for o in outcomes if o.validation is SectionValidation.PASSED)

    if not failed:
        draft.status = DraftStatus.VALIDATED.value
        db.add(draft)
        db.commit()
        # The compliance wave injects its G3 pre-check between validation and advance; None here.
        if post_draft is not None:
            post_draft(db, matter, draft)
        transition = advance(GateState.DRAFTING, GateEvent.DRAFT_COMPLETE)
        matter.gate_state = transition.to.value
        record_event(
            db,
            firm_id=matter.firm_id,
            actor_id=user.id,
            event_kind=_COMPLETED_AUDIT_KIND,
            payload={
                "matter_id": str(matter.id),
                "draft_version": draft.version,
                "plan_version": plan.version,
                "sections_passed": passed,
            },
        )
        db.commit()
        logger.log("gate_advanced", **{"from": GateState.DRAFTING.value, "to": transition.to.value})
        yield format_sse(
            SseEvent.GATE_READY,
            {"gate": "compliance_review", "matter_id": str(matter.id)},
        )
    else:
        # Surfaced-failed section(s): the draft stays DRAFTING, no advance. Visible, not a stall.
        db.commit()
        logger.log("draft_incomplete", failed_sections=failed)
        yield format_sse(
            SseEvent.STATUS,
            {"phase": _PHASE, "state": "draft_incomplete", "failed_sections": failed},
        )

    logger.log(
        "run_completed",
        sections_total=len(sections),
        sections_passed=passed,
        sections_failed=len(failed),
        gate_advanced=not failed,
    )
    yield format_sse(
        SseEvent.STATUS,
        {
            "phase": _PHASE,
            "state": "completed",
            "sections_total": len(sections),
            "sections_passed": passed,
            "sections_failed": len(failed),
            "gate_advanced": not failed,
        },
    )


def _draft_one_section(
    db: Session,
    client: MeteredLLMClient,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    planned: PlannedSection,
    constraints: HardConstraintInputs,
    sort_order: int,
    logger: MatterRunLogger,
) -> Generator[str, None, _SectionOutcome]:
    """Draft, validate, (retry once), and render ONE section; yield its SSE frame(s).

    Returns (via ``return`` in the generator — the caller uses ``yield from``) the
    :class:`_SectionOutcome`. Draft -> validate: clean -> render + a ``section`` frame (``PASSED``).
    Violations -> ONE content retry (violations appended to the prompt tail) -> re-validate: clean
    -> render + a ``section`` frame; still bad -> ``SURFACED_FAILED`` + an ERROR frame with the
    ``{section_id, violations}`` (the run continues). Budget errors propagate to the caller's stop.
    """
    # Attempt 1.
    section = draft_section(
        db,
        client,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=constraints,
        sort_order=sort_order,
    )
    violations = validate_section(
        db, matter=matter, planned=planned, body_tokenized=section.body_tokenized
    )

    if violations:
        logger.log("section_retry", section_id=planned.section_id, violations=violations)
        # One content retry: re-draft with the violations appended, overwriting the row's body +
        # snapshot in place (same section slot, a second attempt — not a new row).
        retried = draft_section(
            db,
            client,
            matter=matter,
            plan=plan,
            draft=draft,
            planned=planned,
            constraints=constraints,
            sort_order=sort_order,
            retry_violations=violations,
        )
        # draft_section created a SECOND row; fold the retry onto the first slot and drop the extra
        # so a section maps to exactly one DraftSection row.
        section.body_tokenized = retried.body_tokenized
        section.prompt_snapshot = retried.prompt_snapshot
        db.delete(retried)
        db.flush()
        violations = validate_section(
            db, matter=matter, planned=planned, body_tokenized=section.body_tokenized
        )

    if violations:
        section.validation = SectionValidation.SURFACED_FAILED.value
        db.add(section)
        db.commit()
        logger.log("section_surfaced_failed", section_id=planned.section_id, violations=violations)
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": _PHASE,
                "error": "section_validation_failed",
                "section_id": planned.section_id,
                "violations": violations,
            },
        )
        return _SectionOutcome(
            section_id=planned.section_id,
            validation=SectionValidation.SURFACED_FAILED,
            violations=tuple(violations),
        )

    # Passed — render (tokens -> display forms + spans) and emit the rendered preview.
    section.validation = SectionValidation.PASSED.value
    render_section(db, matter=matter, section=section)
    db.add(section)
    db.commit()
    logger.log("section_passed", section_id=planned.section_id)
    yield format_sse(
        SseEvent.SECTION,
        {
            "section_id": planned.section_id,
            "rendered_preview": section.rendered_preview,
            "matter_id": str(matter.id),
        },
    )
    return _SectionOutcome(
        section_id=planned.section_id,
        validation=SectionValidation.PASSED,
        violations=(),
    )
