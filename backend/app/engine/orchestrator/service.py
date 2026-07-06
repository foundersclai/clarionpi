"""The gate-action service — persistence + guard wiring around the pure gate machine (M3).

This is the single door every human gate action goes through. The **machine**
(:mod:`~app.engine.orchestrator.machine`) stays pure (state x event -> transition + guard
names); the **guards** (:mod:`~app.engine.orchestrator.guards`) stay pure (context -> result);
this module gathers the context from the DB, applies attorney edits, evaluates legality, runs
registered side-effects, and writes the :class:`~app.models.orm.GateRecord` + audit mirror —
all inside ONE transaction, so a refused action leaves nothing behind (no partial edits on a
refused approve).

Pinned M3 design decisions implemented here (contract docs land in a later wave):

* **D1 — deadline confirm is per-candidate.** ``DeadlineCandidate.confirmed`` is the attorney's
  G1 act (``verify_status`` is the orthogonal lawyer-audit status of the rule text). G1 approve
  requires EVERY candidate on ``matter.sol_candidates`` to be ``confirmed=True`` — invariant 4
  made structural. An empty candidate list is NOT confirmed: a matter with no computed
  deadlines must not slide through G1.
* **D2 — overrides are reasoned acts.** A guard that passes with ``code == "override"``
  requires ``action == "override"`` and a non-blank reason, recorded on the GateRecord
  (allowed-but-logged, never silent).
* **D3 — client-minted idempotency, unique per matter.** A duplicate ``idempotency_key``
  replays the first outcome (the stored record) with the CURRENT matter state; no new record.
* **D4 — side-effects run in-transaction.** Per-(state, event) callables run inside the same
  transaction as the transition: G2A_CONFIRMED freezes the registry version; G25_APPROVED pins the
  matter's latest StrategyPlan (``approved`` + ``approved_by`` + ``approved_at``), refusing with a
  ``GuardRefused``-shaped ``plan_missing`` / ``plan_registry_drift`` when no plan exists / the plan
  is registry-stale.
* **payload_version** ``= matter.registry_version + count(GateRecords for the matter)`` — both
  monotonic, so the sum is a fencing token with no schema change. A stale submit is refused
  with the fresh version (the FE refetch signal).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.matter_budget import BudgetExceededError, assert_within_budget, load_or_create_budget
from app.core.tenancy import tenant_add
from app.engine.orchestrator import guards, machine
from app.engine.orchestrator.guards import GuardContext, GuardResult
from app.engine.orchestrator.idempotency import validate_client_key
from app.models.enums import FlagSeverity, GateAction, GateEvent, GateState, UserRole
from app.models.orm import (
    GateRecord,
    IncidentFacts,
    Matter,
    RegistryVersion,
    RiskFlag,
    StrategyInputs,
    StrategyPlan,
    User,
)
from app.models.schemas import (
    FactsReviewEdits,
    GateSubmit,
    PlannedSection,
    PlanReviewEdits,
    StrategyIntakeEdits,
)

# --------------------------------------------------------------------------------------
# Typed refusals (the API layer maps these to status codes; see routes/gates.py)
# --------------------------------------------------------------------------------------


class GateStateMismatch(Exception):
    """The submitted gate is not the matter's current gate — the FE refetch signal (409)."""

    def __init__(self, *, submitted: str, current: str) -> None:
        self.submitted = submitted
        self.current = current
        super().__init__(f"gate {submitted!r} submitted but matter is at {current!r}")


class StalePayloadVersion(Exception):
    """The submit's payload_version is stale; carries the fresh version to re-fetch (409)."""

    def __init__(self, *, submitted: int, fresh_version: int) -> None:
        self.submitted = submitted
        self.fresh_version = fresh_version
        super().__init__(f"payload_version {submitted} is stale; fresh is {fresh_version}")


class IllegalGateAction(Exception):
    """Approve/override on a state that is not one of the five human-approvable gates (409)."""

    def __init__(self, *, state: str, action: str) -> None:
        self.state = state
        self.action = action
        super().__init__(f"{action} is not a legal action in state {state!r}")


class GuardRefused(Exception):
    """A transition guard failed. Carries the guard name + stable code + human detail.

    The route maps this to 409 ``guard_failed`` — except when ``guard == "role_attorney"``,
    which keeps the auth-shaped 403 ``role_forbidden`` status for the FE (invariant 8).
    """

    def __init__(self, *, guard: str, code: str, detail: str) -> None:
        self.guard = guard
        self.code = code
        self.detail = detail
        super().__init__(f"guard {guard} refused ({code}): {detail}")


class PlanMissing(GuardRefused):
    """G2.5 approve on a matter with no emitted StrategyPlan — a plan must exist to approve.

    A :class:`GuardRefused` subclass so the route maps it with ZERO new mapping code: it surfaces
    as the 409 ``guard_failed`` body ``{"guard": "strategy_plan", "code": "plan_missing", ...}``.
    Raised by the G2.5 side effect (the guard table's ``registry_version_match`` covers the
    matter-level pin; this covers "there is a plan at all").
    """

    def __init__(self) -> None:
        super().__init__(
            guard="strategy_plan",
            code="plan_missing",
            detail="no StrategyPlan for the matter; emit a plan (plan/emit) before approving G2.5",
        )


class PlanRegistryDrift(GuardRefused):
    """G2.5 approve when the latest plan's ``registry_version`` != the matter's — the plan is stale.

    A :class:`GuardRefused` subclass -> 409 ``guard_failed`` ``{"guard": "strategy_plan", "code":
    "plan_registry_drift", ...}``. Distinct from the transition's ``registry_version_match`` guard:
    THAT guard pins the matter to its frozen registry version (the matter-level pin); THIS pins the
    *plan row itself* to the matter's current registry version (the plan-level bind — a plan emitted
    before a later freeze is stale even if the matter's own pin still holds). Both must agree to
    approve.
    """

    def __init__(self, *, plan_version: int, matter_version: int) -> None:
        self.plan_version = plan_version
        self.matter_version = matter_version
        super().__init__(
            guard="strategy_plan",
            code="plan_registry_drift",
            detail=f"latest plan registry_version {plan_version} != matter registry_version "
            f"{matter_version}; re-emit the plan at the current version",
        )


class OverrideRequired(Exception):
    """The approve needs an audited override — re-submit as ``action="override"`` + reason (409).

    Raised both when the overridable guard *failed* for lack of a reason
    (``code == "high_severity_open"``) and when it *passed* via override on a plain approve
    (``code == "override"`` but ``action != override``) — either way the FE's next move is the
    same: re-submit as an override with a reason.
    """

    def __init__(self, *, guard: str, code: str, detail: str) -> None:
        self.guard = guard
        self.code = code
        self.detail = detail
        super().__init__(f"override required at guard {guard} ({code}): {detail}")


class OverrideReasonRequired(Exception):
    """``action="override"`` with a blank/absent reason — an override IS a reasoned act (422)."""

    def __init__(self) -> None:
        super().__init__("action 'override' requires a non-blank override_reason (design D2)")


class UnknownDeadlineRule(Exception):
    """A deadline confirmation named a rule_id not on the matter's candidates (422)."""

    def __init__(self, *, rule_id: str) -> None:
        self.rule_id = rule_id
        super().__init__(f"no deadline candidate with rule_id {rule_id!r} on this matter")


class UnknownPlanSection(Exception):
    """A G2.5 plan edit named a ``section_id`` not on the latest plan's skeleton (422).

    A plan edit refines existing planned sections (matched by ``section_id``); it must not invent a
    section (the skeleton is pack-driven, never invented — brain2). An unknown id refuses the whole
    edit, so a typo'd section edit can never half-apply.
    """

    def __init__(self, *, section_id: str) -> None:
        self.section_id = section_id
        super().__init__(f"no planned section with section_id {section_id!r} on the latest plan")


class EditsNotSupported(Exception):
    """Non-empty edits submitted at a gate with no edit surface at M3 (422)."""

    def __init__(self, *, gate: str) -> None:
        self.gate = gate
        super().__init__(f"edits are not supported at gate {gate!r} at M3")


class InvalidEdits(Exception):
    """The edits payload does not validate against the gate's edit schema (422)."""

    def __init__(self, *, gate: str, detail: str) -> None:
        self.gate = gate
        self.detail = detail
        super().__init__(f"invalid edits for gate {gate!r}: {detail}")


# --------------------------------------------------------------------------------------
# The approve-event map — ONLY the five human-approvable gates
# --------------------------------------------------------------------------------------

GATE_EVENT_BY_APPROVE: Mapping[GateState, GateEvent] = {
    GateState.FACTS_REVIEW: GateEvent.G1_APPROVED,
    GateState.STRATEGY_INTAKE: GateEvent.G15_SUBMITTED,
    GateState.EVIDENCE_REVIEW: GateEvent.G2A_CONFIRMED,
    GateState.PLAN_REVIEW: GateEvent.G25_APPROVED,
    GateState.COMPLIANCE_REVIEW: GateEvent.G3_APPROVED,
}

# Gates with an edit surface: G1 facts + G1.5 strategy (M3), G2.5 plan (M5 — the plan edit
# re-emits a new StrategyPlan version, "edits re-emit the plan, not prose", C5).
_EDITABLE_GATES: frozenset[GateState] = frozenset(
    {GateState.FACTS_REVIEW, GateState.STRATEGY_INTAKE, GateState.PLAN_REVIEW}
)


# --------------------------------------------------------------------------------------
# Result shape
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GateActionResult:
    """Outcome of one applied (or replayed) gate action."""

    matter: Matter
    record: GateRecord
    transitioned: bool
    from_state: str
    to_state: str
    replayed: bool


# --------------------------------------------------------------------------------------
# payload_version — the no-schema fencing token
# --------------------------------------------------------------------------------------


def payload_version(db: Session, *, matter: Matter) -> int:
    """``matter.registry_version + count(GateRecords for the matter)``.

    Both terms are monotonic (registry versions only bump; gate records are append-only), so
    the sum strictly increases on every state-changing act — an optimistic-concurrency fence
    with no schema change. Exposed for the GET envelope so the FE echoes exactly this.
    """
    record_count = db.execute(
        select(func.count()).select_from(GateRecord).where(GateRecord.matter_id == matter.id)
    ).scalar_one()
    return matter.registry_version + record_count


# --------------------------------------------------------------------------------------
# Guard context assembly
# --------------------------------------------------------------------------------------


def deadlines_all_confirmed(matter: Matter) -> bool:
    """Design D1 as one predicate: non-empty candidates AND every one ``confirmed is True``.

    An empty candidate list is ``False`` — a matter with no computed deadlines must not slide
    through G1 (invariant 4). Shared by the guard context and the strategy-intake view-model
    so the rule lives exactly once.
    """
    candidates = matter.sol_candidates or []
    return bool(candidates) and all(
        isinstance(c, dict) and c.get("confirmed") is True for c in candidates
    )


def build_guard_context(
    db: Session, *, matter: Matter, user: User, override_reason: str | None
) -> GuardContext:
    """Gather everything the pure guards need from the DB — the caller-side of guards.py.

    * ``deadlines_confirmed``: ``sol_candidates`` non-empty AND every candidate dict has
      ``confirmed is True``. An EMPTY list is ``False`` — a matter with no computed deadlines
      must not slide through G1 (invariant 4; pinned design D1).
    * ``budget_available``: spend strictly under cap, via the matter_budget helpers (the row is
      created at the default cap on first use; nothing here commits spend).
    * ``registry_version_pinned``: the latest FROZEN RegistryVersion's version for the matter,
      or ``None`` when nothing is frozen yet (M5 refines this to the plan-pinned version).
    * ``open_high_severity_flags``: count of high-severity RiskFlags with no disposition.
    * ``blocking_findings``: open blocking compliance findings on the matter's LATEST
      :class:`~app.models.orm.DemandDraft` (0 when there is no draft) — the M5 compliance panel's
      ``open_blocking_count`` over that draft. This feeds the G3 ``no_blocking_findings`` guard.
    """
    # Lazy import: the compliance engine pulls in brain2 + package + money; importing it at the top
    # of this widely-imported service module would broaden the import graph for every caller.
    from app.engine.compliance.engine import latest_draft, open_blocking_count

    deadlines_confirmed = deadlines_all_confirmed(matter)

    budget = load_or_create_budget(db, firm_id=matter.firm_id, matter_id=matter.id)
    try:
        assert_within_budget(budget)
        budget_available = True
    except BudgetExceededError:
        budget_available = False

    pinned = db.execute(
        select(func.max(RegistryVersion.version)).where(
            RegistryVersion.matter_id == matter.id,
            RegistryVersion.frozen.is_(True),
        )
    ).scalar_one_or_none()

    open_high = db.execute(
        select(func.count())
        .select_from(RiskFlag)
        .where(
            RiskFlag.matter_id == matter.id,
            RiskFlag.severity == FlagSeverity.HIGH.value,
            RiskFlag.disposition.is_(None),
        )
    ).scalar_one()

    draft = latest_draft(db, matter=matter)
    blocking_findings = (
        open_blocking_count(db, matter=matter, draft=draft) if draft is not None else 0
    )

    return GuardContext(
        actor_role=UserRole(user.role),
        deadlines_confirmed=deadlines_confirmed,
        budget_available=budget_available,
        registry_version_pinned=pinned,
        registry_version_current=matter.registry_version,
        open_high_severity_flags=open_high,
        override_reason=override_reason,
        blocking_findings=blocking_findings,
    )


def _guard_results(
    transition: machine.Transition, ctx: GuardContext
) -> list[tuple[str, GuardResult]]:
    """Every guard of ``transition`` with its name, in table order.

    Same semantics as :func:`guards.evaluate` (all results, not first-fail) but keeping the
    guard NAME alongside each result — the typed refusals and the affordance dry-run both need
    to say *which* guard spoke, and ``GuardResult`` does not carry its name.
    """
    return [(name, guards.REGISTRY[name](ctx)) for name in transition.guards]


def dry_run_approve_blockers(db: Session, *, matter: Matter, user: User) -> list[dict]:
    """Evaluate the approve guards for the CURRENT state/context with NO side effects.

    Backs the GET envelope's ``role_affordances.approve_blockers``: each failing guard as
    ``{"guard", "code", "detail"}``. A state outside the five approvable gates returns ``[]``
    (there is nothing to dry-run; ``can_approve`` is already false). The override-only path
    shows up as its natural blocker (``high_severity_open``) since a dry run carries no reason.
    """
    state = GateState(matter.gate_state)
    event = GATE_EVENT_BY_APPROVE.get(state)
    if event is None:
        return []
    transition = machine.advance(state, event)
    ctx = build_guard_context(db, matter=matter, user=user, override_reason=None)
    return [
        {"guard": name, "code": result.code, "detail": result.detail}
        for name, result in _guard_results(transition, ctx)
        if not result.passed
    ]


# --------------------------------------------------------------------------------------
# Side-effect registry (design D4: same transaction as the transition)
# --------------------------------------------------------------------------------------


def _freeze_registry_version(db: Session, *, matter: Matter, user: User) -> None:
    """G2a confirm freezes the registry at the current version (flow_02 freeze).

    Marks the matter's RegistryVersion row at ``matter.registry_version`` as ``frozen=True``;
    if the matter has never bumped (version 0, no rows), the row is created frozen at the
    current version so the pin exists. Runs inside the action's transaction — an approve that
    fails to freeze fails whole.
    """
    row = db.execute(
        select(RegistryVersion).where(
            RegistryVersion.matter_id == matter.id,
            RegistryVersion.version == matter.registry_version,
        )
    ).scalar_one_or_none()
    if row is None:
        row = RegistryVersion(
            matter_id=matter.id,
            version=matter.registry_version,
            frozen=True,
            parent_version=None,
            change_reason="g2a_freeze",
        )
        tenant_add(db, row, matter.firm_id)
    else:
        row.frozen = True


def _latest_plan(db: Session, *, matter: Matter) -> StrategyPlan | None:
    """The matter's highest-``version`` :class:`StrategyPlan`, or ``None`` (none emitted yet)."""
    plans = list(
        db.execute(select(StrategyPlan).where(StrategyPlan.matter_id == matter.id)).scalars()
    )
    if not plans:
        return None
    return max(plans, key=lambda p: p.version)


def _naive_utc_now() -> datetime:
    """Wall-clock UTC as a naive datetime (house convention; see ``core.auth._utcnow_naive``)."""
    return datetime.now(UTC).replace(tzinfo=None)


def _approve_plan_version(db: Session, *, matter: Matter, user: User) -> None:
    """G2.5 approve: pin + stamp the matter's LATEST StrategyPlan as attorney-approved.

    Runs inside the action's transaction (design D4). Refusals (raised as :class:`GuardRefused`
    subclasses so the route maps them to the 409 ``guard_failed`` body with no new mapping):

    * no plan on the matter -> :class:`PlanMissing` (a plan must be emitted before G2.5 approve);
    * the latest plan's ``registry_version`` != the matter's -> :class:`PlanRegistryDrift` (the
      plan-level bind; the transition's ``registry_version_match`` guard covers the matter-level
      pin — see :class:`PlanRegistryDrift` for the distinction).

    Otherwise: ``approved=True``, ``approved_by=user.id``, ``approved_at`` = naive-UTC now (the
    GateRecord stays the authoritative approval trail; these are the plan-row denorm). The
    DemandDraft's bind to ``plan.version`` happens at generation (Wave B1), not here.
    """
    plan = _latest_plan(db, matter=matter)
    if plan is None:
        raise PlanMissing()
    if plan.registry_version != matter.registry_version:
        raise PlanRegistryDrift(
            plan_version=plan.registry_version, matter_version=matter.registry_version
        )
    plan.approved = True
    plan.approved_by = user.id
    plan.approved_at = _naive_utc_now()
    db.add(plan)


_SIDE_EFFECTS: Mapping[tuple[GateState, GateEvent], Callable[..., None]] = {
    (GateState.EVIDENCE_REVIEW, GateEvent.G2A_CONFIRMED): _freeze_registry_version,
    (GateState.PLAN_REVIEW, GateEvent.G25_APPROVED): _approve_plan_version,
}


# --------------------------------------------------------------------------------------
# Edits — validated + applied per gate
# --------------------------------------------------------------------------------------


def _edits_payload(
    edits: FactsReviewEdits | StrategyIntakeEdits | PlanReviewEdits | dict | None,
) -> dict:
    """Normalize the submit's edits union to a plain dict of SET fields.

    ``exclude_unset`` matters: the union parses ``{}`` into a defaulted ``FactsReviewEdits``,
    and dumping defaults back would smuggle facts-shaped keys into a strategy-gate validation.
    Only what the client actually sent survives.
    """
    if edits is None:
        return {}
    if isinstance(edits, BaseModel):
        return edits.model_dump(exclude_unset=True)
    return dict(edits)


def _apply_facts_review_edits(db: Session, *, matter: Matter, user: User, payload: dict) -> None:
    """Apply G1 edits: per-candidate confirmations + IncidentFacts payload merge.

    Confirmation matching is by ``rule_id == candidate["statute_cite"]`` (the candidate's only
    stable identifier — see :class:`~app.models.schemas.DeadlineConfirmation`). An unknown
    rule_id refuses the whole action (typed 422), so a typo'd confirm can never half-apply.
    JSON columns are REASSIGNED (not mutated in place) so SQLAlchemy sees the change.
    """
    try:
        edits = FactsReviewEdits.model_validate(payload)
    except ValidationError as exc:
        raise InvalidEdits(gate=GateState.FACTS_REVIEW.value, detail=str(exc)) from exc

    if edits.deadline_confirmations:
        candidates = [dict(c) for c in (matter.sol_candidates or [])]
        by_rule_id = {c.get("statute_cite"): c for c in candidates}
        for confirmation in edits.deadline_confirmations:
            candidate = by_rule_id.get(confirmation.rule_id)
            if candidate is None:
                raise UnknownDeadlineRule(rule_id=confirmation.rule_id)
            candidate["confirmed"] = confirmation.confirmed
        matter.sol_candidates = candidates  # reassign: JSON column change detection

    if edits.incident_facts is not None:
        row = db.execute(
            select(IncidentFacts).where(IncidentFacts.matter_id == matter.id)
        ).scalar_one_or_none()
        if row is None:
            # Attorney-supplied intake facts may precede any police-report extraction; the row
            # is created with no anchors (these facts are attorney-attested, not doc-anchored).
            row = IncidentFacts(matter_id=matter.id, payload=dict(edits.incident_facts))
            tenant_add(db, row, matter.firm_id)
        else:
            row.payload = {**dict(row.payload or {}), **edits.incident_facts}  # reassign


def _apply_strategy_intake_edits(db: Session, *, matter: Matter, user: User, payload: dict) -> None:
    """Apply G1.5 edits: upsert the StrategyInputs row, only non-None fields, VERBATIM.

    Attorney text is stored exactly as typed — no strip(), no whitespace normalization, no
    casing: the strategy inputs are the attorney's voice and the Brain-2 signal downstream
    (verbatim rule). ``None`` means "not provided", so a field can be set but not cleared
    through this path at M3.
    """
    try:
        edits = StrategyIntakeEdits.model_validate(payload)
    except ValidationError as exc:
        raise InvalidEdits(gate=GateState.STRATEGY_INTAKE.value, detail=str(exc)) from exc

    row = db.execute(
        select(StrategyInputs).where(StrategyInputs.matter_id == matter.id)
    ).scalar_one_or_none()
    if row is None:
        row = StrategyInputs(matter_id=matter.id)
        tenant_add(db, row, matter.firm_id)

    for field in (
        "liability_theory",
        "injury_framing",
        "emphasis_notes",
        "venue_posture",
        "anchor_amount_cents",
        "mmi_date",
        "property_damage_estimate_cents",
    ):
        value = getattr(edits, field)
        if value is not None:
            setattr(row, field, value)


def _next_plan_version(db: Session, *, matter: Matter) -> int:
    """One past the count of existing plans for the matter (as ``brain2.plan._next_version``)."""
    existing = list(
        db.execute(select(StrategyPlan.id).where(StrategyPlan.matter_id == matter.id)).scalars()
    )
    return len(existing) + 1


def _apply_plan_review_edits(db: Session, *, matter: Matter, user: User, payload: dict) -> None:
    """Apply G2.5 edits: copy the latest StrategyPlan into a NEW unapproved version + overrides.

    "Edits re-emit the plan, not prose" (C5). Steps:

    * validate the payload against :class:`~app.models.schemas.PlanReviewEdits` (closed; a bad key /
      a rejected ``demand_type`` is a typed 422 ``invalid_edits``);
    * require a latest plan to copy (:class:`PlanMissing` if none — a plan must be emitted before it
      can be edited);
    * copy the latest plan's ``sections`` / ``emphasis_directives`` / ``demand_*``; apply the
      top-level non-``None`` fields; merge each :class:`~app.models.schemas.PlannedSectionEdit` onto
      its section by ``section_id`` (an unknown id -> :class:`UnknownPlanSection` 422; the emitted
      skeleton is the source of truth, an edit never invents a section);
    * write the new row (``version = count+1``, ``registry_version = matter.registry_version``,
      ``approved=False``), audit ``strategy_plan_edited`` (uncommitted — the action commits).
    """
    try:
        edits = PlanReviewEdits.model_validate(payload)
    except ValidationError as exc:
        raise InvalidEdits(gate=GateState.PLAN_REVIEW.value, detail=str(exc)) from exc

    latest = _latest_plan(db, matter=matter)
    if latest is None:
        # There is nothing to refine — a plan must be emitted (plan/emit) before it can be edited.
        raise PlanMissing()

    # Start from the latest plan's allocation (deep-copied dicts so JSON columns detect the change).
    sections_by_id: dict[str, dict] = {}
    ordered_ids: list[str] = []
    for raw in latest.sections:
        section = PlannedSection.model_validate(raw).model_dump()
        sections_by_id[section["section_id"]] = section
        ordered_ids.append(section["section_id"])

    if edits.sections is not None:
        for section_edit in edits.sections:
            target = sections_by_id.get(section_edit.section_id)
            if target is None:
                raise UnknownPlanSection(section_id=section_edit.section_id)
            if section_edit.max_words is not None:
                target["max_words"] = section_edit.max_words
            if section_edit.allowed_tokens is not None:
                target["allowed_tokens"] = list(section_edit.allowed_tokens)
            if section_edit.required_tokens is not None:
                target["required_tokens"] = list(section_edit.required_tokens)

    new_plan = StrategyPlan(
        matter_id=matter.id,
        version=_next_plan_version(db, matter=matter),
        registry_version=matter.registry_version,
        demand_amount_cents=(
            edits.demand_amount_cents
            if edits.demand_amount_cents is not None
            else latest.demand_amount_cents
        ),
        demand_type=edits.demand_type if edits.demand_type is not None else latest.demand_type,
        sections=[sections_by_id[sid] for sid in ordered_ids],
        emphasis_directives=(
            list(edits.emphasis_directives)
            if edits.emphasis_directives is not None
            else list(latest.emphasis_directives or [])
        ),
        approved=False,
    )
    tenant_add(db, new_plan, matter.firm_id)
    db.flush()  # assign new_plan.id for the audit payload
    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind="strategy_plan_edited",
        payload={
            "matter_id": str(matter.id),
            "plan_id": str(new_plan.id),
            "version": new_plan.version,
            "from_version": latest.version,
            "registry_version": new_plan.registry_version,
        },
    )


def _validate_and_apply_edits(
    db: Session, *, matter: Matter, user: User, state: GateState, submit: GateSubmit
) -> None:
    """Step 4: per-gate edits validation + application (uncommitted — the action commits).

    Runs for every action (spec step order): edit and approve both accept edits, so
    edits-then-approve is one atomic call. Gates outside the edit surface refuse any non-empty
    edits with a typed 422.
    """
    payload = _edits_payload(submit.edits)
    if state not in _EDITABLE_GATES:
        if payload:
            raise EditsNotSupported(gate=state.value)
        return
    if not payload:
        return
    if state is GateState.FACTS_REVIEW:
        _apply_facts_review_edits(db, matter=matter, user=user, payload=payload)
    elif state is GateState.STRATEGY_INTAKE:
        _apply_strategy_intake_edits(db, matter=matter, user=user, payload=payload)
    else:  # GateState.PLAN_REVIEW (the M5 edit surface — re-emits a new plan version)
        _apply_plan_review_edits(db, matter=matter, user=user, payload=payload)


# --------------------------------------------------------------------------------------
# The action entry point
# --------------------------------------------------------------------------------------


def _payload_hash(submit: GateSubmit) -> str:
    """sha256 of the canonical submit JSON (sorted keys, tight separators) — audit anchor."""
    canonical = json.dumps(submit.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evaluate_approve_guards(
    *, transition: machine.Transition, ctx: GuardContext, action: GateAction
) -> None:
    """Evaluate ALL of the transition's guards; raise the typed refusal for the first failure.

    Failure mapping (guard order is table order, so ``role_attorney`` — always first on the
    approve edges — wins ties, keeping the auth-shaped refusal first):

    * ``role_attorney`` fail -> :class:`GuardRefused` (the route maps this one to 403).
    * the overridable guard fail (``high_severity_open``) -> :class:`OverrideRequired` — the
      FE's next move is a reasoned override, not a different fix.
    * any other fail -> :class:`GuardRefused` (409).
    * every guard passed, but one passed with ``code == "override"`` while the action is a
      plain approve -> :class:`OverrideRequired` — an override outcome must be *asked for*
      (design D2: allowed-but-logged, never an accidental side effect of approve).
    """
    results = _guard_results(transition, ctx)
    for name, result in results:
        if result.passed:
            continue
        if result.code == "high_severity_open":
            raise OverrideRequired(guard=name, code=result.code, detail=result.detail)
        raise GuardRefused(guard=name, code=result.code, detail=result.detail)
    if action is not GateAction.OVERRIDE:
        for name, result in results:
            if result.code == "override":
                raise OverrideRequired(guard=name, code=result.code, detail=result.detail)


def apply_gate_action(
    db: Session, *, matter: Matter, user: User, gate: str, submit: GateSubmit
) -> GateActionResult:
    """Apply one gate action atomically. See the module doc for the pinned design.

    Steps (order matters — each comment names the invariant it enforces):

    1. gate == matter.gate_state, else :class:`GateStateMismatch` (the FE refetch signal).
    2. idempotent replay: an existing (matter, idempotency_key) record returns the FIRST
       outcome's record with the CURRENT matter state — replay never re-executes (D3).
    3. payload_version fence, else :class:`StalePayloadVersion` with the fresh version.
    4. per-gate edits validation + application (uncommitted).
    5. action dispatch — edit/reject record without transition; approve/override run the
       machine + guards + side-effects and move ``matter.gate_state``.
    6. ONE commit at the end; ANY typed refusal rolls back, so a refused approve leaves its
       edits unapplied (atomicity: edits and approve succeed or fail together).
    """
    try:
        return _apply_gate_action_inner(db, matter=matter, user=user, gate=gate, submit=submit)
    except Exception:
        # One transaction (step 6): every typed refusal above rolled nothing back itself; do it
        # here once so no partial edit/record survives a refused action.
        db.rollback()
        raise


def _apply_gate_action_inner(
    db: Session, *, matter: Matter, user: User, gate: str, submit: GateSubmit
) -> GateActionResult:
    # -- 1. the submitted gate must be the matter's current gate (stale-tab guard) --------
    if gate != matter.gate_state:
        raise GateStateMismatch(submitted=gate, current=matter.gate_state)
    state = GateState(matter.gate_state)

    # Key format check (reuses the M0 idempotency module; raises InvalidIdempotencyKey -> 422).
    validate_client_key(submit.idempotency_key)

    # -- 2. idempotent replay (D3): the first outcome's record, the CURRENT state ---------
    # NOTE deliberate consequence of the pinned step order: a duplicate of an approve that
    # already transitioned re-arrives addressed to the OLD gate and is answered by step 1's
    # gate_state_mismatch (the refetch signal), not a replay — replay serves duplicates that
    # did not move the state (edit/reject retries and same-state races).
    existing = db.execute(
        select(GateRecord).where(
            GateRecord.matter_id == matter.id,
            GateRecord.idempotency_key == submit.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return GateActionResult(
            matter=matter,
            record=existing,
            transitioned=False,
            from_state=matter.gate_state,
            to_state=matter.gate_state,
            replayed=True,
        )

    # -- 3. payload_version fence (optimistic concurrency; no schema change) --------------
    fresh = payload_version(db, matter=matter)
    if submit.payload_version != fresh:
        raise StalePayloadVersion(submitted=submit.payload_version, fresh_version=fresh)

    # An override is a reasoned act (D2) — refuse a blank reason before any state is touched.
    if submit.action is GateAction.OVERRIDE and not (submit.override_reason or "").strip():
        raise OverrideReasonRequired()

    # -- 4. per-gate edits (uncommitted; atomic with the action outcome) -------------------
    _validate_and_apply_edits(db, matter=matter, user=user, state=state, submit=submit)

    from_state = matter.gate_state
    transitioned = False

    # -- 5. action dispatch ----------------------------------------------------------------
    if submit.action in (GateAction.APPROVE, GateAction.OVERRIDE):
        event = GATE_EVENT_BY_APPROVE.get(state)
        if event is None:
            # Only the five human gates are approvable; auto states advance on system events.
            raise IllegalGateAction(state=state.value, action=submit.action.value)
        transition = machine.advance(state, event)
        ctx = build_guard_context(
            db, matter=matter, user=user, override_reason=submit.override_reason
        )
        _evaluate_approve_guards(transition=transition, ctx=ctx, action=submit.action)
        # Guards passed: side-effects run INSIDE this transaction (D4), then the state moves.
        side_effect = _SIDE_EFFECTS.get((state, event))
        if side_effect is not None:
            side_effect(db, matter=matter, user=user)
        matter.gate_state = transition.to.value
        transitioned = True
    # edit / reject: record + audit, NO transition (a rejected G-gate parks in place — the
    # matter stays at the gate for rework; nothing in the machine moves on a human reject).

    record = GateRecord(
        matter_id=matter.id,
        gate=from_state,
        action=submit.action.value,
        actor_id=user.id,
        actor_role=user.role,
        payload_hash=_payload_hash(submit),
        override_reason=submit.override_reason,
        idempotency_key=submit.idempotency_key,
    )
    tenant_add(db, record, matter.firm_id)
    db.flush()  # assign record.id for the audit payload

    # Audit mirror (invariant 9): synchronous, same transaction — an audit failure fails the act.
    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind="gate_action",
        payload={
            "matter_id": str(matter.id),
            "gate": from_state,
            "action": submit.action.value,
            "record_id": str(record.id),
            "from_state": from_state,
            "to_state": matter.gate_state,
            "transitioned": transitioned,
            "override_reason": submit.override_reason,
            "payload_hash": record.payload_hash,
        },
    )

    # -- 6. ONE commit for edits + record + side-effects + state move ----------------------
    db.commit()
    return GateActionResult(
        matter=matter,
        record=record,
        transitioned=transitioned,
        from_state=from_state,
        to_state=matter.gate_state,
        replayed=False,
    )


__all__ = [
    "GATE_EVENT_BY_APPROVE",
    "EditsNotSupported",
    "GateActionResult",
    "GateStateMismatch",
    "GuardRefused",
    "IllegalGateAction",
    "InvalidEdits",
    "OverrideReasonRequired",
    "OverrideRequired",
    "PlanMissing",
    "PlanRegistryDrift",
    "StalePayloadVersion",
    "UnknownDeadlineRule",
    "UnknownPlanSection",
    "apply_gate_action",
    "build_guard_context",
    "deadlines_all_confirmed",
    "dry_run_approve_blockers",
    "payload_version",
]
