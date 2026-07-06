"""G3 corrections — span-patch, single-section regen, and the mandatory re-verify (inv 2, 13).

Two fix paths and one gate:

* :func:`apply_span_patch` — the MECHANICAL fix: a deterministic re-render of the finding's
  section (:func:`app.engine.brain2.renderer.render_section`), NO LLM. Tokens re-resolve to fresh
  display forms, so an AMT fix lands by re-resolution and an exhibit fix by an upstream re-mint. If
  the re-rendered section fails deterministic validation
  (:func:`app.engine.brain2.validator.validate_section`), the patch is abandoned and the finding is
  **escalated to the semantic bucket** (regen) — the TM span-patch-with-runtime-fallback safety net:
  a mechanical splice that would land an invalid section falls back to a full regen rather than
  shipping the splice.

* :func:`request_section_regen` — the SEMANTIC fix: re-draft the planned section via Brain-2 with
  the finding's detail handed to the drafter as a fix instruction, then re-validate + re-render and
  replace the section row content IN PLACE (same row id). The fix instruction is passed through the
  drafter's ``retry_violations`` channel (the prompt tail) rather than as an extra hard-constraint
  entry — a **deliberate deviation** from a literal "add a HardConstraintInputs entry" reading:
  ``retry_violations`` is snapshot-NEUTRAL (the drafter builds its snapshot without it), so the
  regenerated section's ``prompt_snapshot`` still reproduces from ``build_hard_constraints`` and the
  re-verify judge never spuriously raises :class:`~app.engine.compliance.judge.SnapshotDrift` on a
  legitimately-regenerated section. Appending a hard-constraint entry would break that symmetry.

  **State stays put (M5 simplification).** Regen happens in place at ``compliance_review`` WITHOUT
  the ``(COMPLIANCE_REVIEW, SEMANTIC_FINDING_REGEN) -> DRAFTING`` machine round-trip. Those machine
  edges exist for the FE's long-form flow (the API wave may wire them); the engine stays
  state-agnostic — re-verify covers correctness, and the gate never advanced, so re-drafting a
  section in place is sound.

* :func:`re_verify` — **always runs after a patch or regen** (the engine enforces it from both
  paths' callers): re-run the deterministic pass (and the judge for regenerated sections when a
  client is given). A finding whose condition no longer reproduces flips to ``re_verified``;
  still-failing ones stay ``open``; any NEW finding a fix introduced (e.g. a fresh orphan) is
  created ``open`` — the "a fix that introduces a new orphan is caught" rule made real.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.llm_telemetry import MeteredLLMClient
from app.engine.brain2.constraints import build_hard_constraints
from app.engine.brain2.drafter import draft_section
from app.engine.brain2.renderer import render_section
from app.engine.brain2.validator import validate_section
from app.models.enums import FindingBucket, FindingStatus, SectionValidation
from app.models.orm import ComplianceFinding, DemandDraft, DraftSection, Matter, StrategyPlan
from app.models.schemas import PlannedSection

_LOG = logging.getLogger("clarionpi.compliance.corrections")


class SectionNotFound(Exception):
    """A finding references a section id with no matching :class:`DraftSection` row on the draft."""

    def __init__(self, *, draft_id: object, section_id: str) -> None:
        self.draft_id = draft_id
        self.section_id = section_id
        super().__init__(f"no draft section {section_id!r} on draft {draft_id}")


class PlanNotFound(Exception):
    """The draft's :class:`StrategyPlan` version could not be loaded (regen needs the contract)."""

    def __init__(self, *, matter_id: object, plan_version: int) -> None:
        self.matter_id = matter_id
        self.plan_version = plan_version
        super().__init__(f"no strategy plan v{plan_version} for matter {matter_id}")


def _section_for_finding(
    db: Session, *, draft: DemandDraft, finding: ComplianceFinding
) -> DraftSection:
    """The :class:`DraftSection` a finding anchors to, or raise :class:`SectionNotFound`."""
    section = db.execute(
        select(DraftSection).where(
            DraftSection.draft_id == draft.id,
            DraftSection.section_id == finding.section_id,
        )
    ).scalar_one_or_none()
    if section is None:
        raise SectionNotFound(draft_id=draft.id, section_id=finding.section_id)
    return section


def _plan_for_draft(db: Session, *, matter: Matter, draft: DemandDraft) -> StrategyPlan:
    """The :class:`StrategyPlan` the draft was drafted from (by version), or raise."""
    plan = db.execute(
        select(StrategyPlan).where(
            StrategyPlan.matter_id == matter.id,
            StrategyPlan.version == draft.strategy_plan_version,
        )
    ).scalar_one_or_none()
    if plan is None:
        raise PlanNotFound(matter_id=matter.id, plan_version=draft.strategy_plan_version)
    return plan


def _planned_section(plan: StrategyPlan, *, section_id: str) -> PlannedSection | None:
    """The planned contract for a section id from the plan, or ``None``."""
    for raw in plan.sections:
        ps = PlannedSection.model_validate(raw)
        if ps.section_id == section_id:
            return ps
    return None


def apply_span_patch(
    db: Session, *, matter: Matter, draft: DemandDraft, finding: ComplianceFinding
) -> ComplianceFinding:
    """Mechanically re-render the finding's section; on validation failure, escalate to regen.

    Deterministic (no LLM): re-renders the whole section
    (:func:`app.engine.brain2.renderer.render_section`) so every token re-resolves to a fresh
    display form — an AMT fix lands by re-resolution, an exhibit fix by an upstream re-mint. Then
    re-validates the section deterministically:

    * clean -> ``finding.status = PATCHED``;
    * any violation -> RUNTIME FALLBACK: the finding's ``bucket`` is set to ``semantic`` (escalate
      to a regen; the TM safety net), it is returned UN-patched with ``status`` still ``OPEN`` and
      its detail appended ``"; span-patch failed validation -> regen"``.

    Commits either way. Raises :class:`SectionNotFound` / :class:`PlanNotFound` if the section /
    plan cannot be located.
    """
    section = _section_for_finding(db, draft=draft, finding=finding)
    plan = _plan_for_draft(db, matter=matter, draft=draft)
    planned = _planned_section(plan, section_id=finding.section_id)

    # Deterministic re-render — tokens re-resolve to fresh display forms (no model call).
    render_section(db, matter=matter, section=section)

    violations: list[str] = []
    if planned is not None:
        violations = validate_section(
            db, matter=matter, planned=planned, body_tokenized=section.body_tokenized
        )

    if violations:
        # Runtime fallback: the mechanical splice would land an invalid section — escalate to
        # regen (drop the finding into the semantic bucket) rather than ship the splice.
        finding.bucket = FindingBucket.SEMANTIC.value
        if "span-patch failed validation -> regen" not in (finding.detail or ""):
            finding.detail = (finding.detail or "") + "; span-patch failed validation -> regen"
        # status stays OPEN.
        _LOG.warning(
            "span-patch for section %s failed validation (%d violation(s)); escalating to regen",
            finding.section_id,
            len(violations),
        )
    else:
        finding.status = FindingStatus.PATCHED.value

    db.add(finding)
    db.add(section)
    db.commit()
    return finding


def request_section_regen(
    db: Session,
    client: MeteredLLMClient,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    finding: ComplianceFinding,
) -> tuple[DraftSection, ComplianceFinding]:
    """Re-draft the finding's planned section via Brain-2 and replace the row content in place.

    Re-drafts the section (:func:`app.engine.brain2.drafter.draft_section`) with the finding's
    detail handed to the drafter as a fix instruction through the ``retry_violations`` channel
    (the prompt tail — snapshot-neutral, so the regenerated snapshot still reproduces from
    ``build_hard_constraints``; see the module doc). The single content-retry discipline is
    Brain-2's. The freshly-drafted body/snapshot replace the EXISTING row (same row id — spans and
    rendered preview are re-minted), the section is re-validated + re-rendered, and
    ``finding.status = REGENERATED``.

    The caller (engine / wiring) owns any machine event; the engine stays state-agnostic (regen is
    in place at ``compliance_review`` — the M5 simplification documented in the module doc).
    Commits. Raises :class:`SectionNotFound` / a plan-shape error if the section / planned contract
    is missing.
    """
    section = _section_for_finding(db, draft=draft, finding=finding)
    planned = _planned_section(plan, section_id=finding.section_id)
    if planned is None:
        raise PlanNotFound(matter_id=matter.id, plan_version=plan.version)

    constraints = build_hard_constraints(db, matter=matter)
    fix_instruction = [f"G3 finding to fix: {finding.detail}"]

    # Re-draft: draft_section creates a NEW row; fold it onto the existing slot and drop the extra
    # so a section maps to exactly one DraftSection row (mirrors generate._draft_one_section).
    regenerated = draft_section(
        db,
        client,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=constraints,
        sort_order=section.sort_order,
        retry_violations=fix_instruction,
    )
    section.body_tokenized = regenerated.body_tokenized
    section.prompt_snapshot = regenerated.prompt_snapshot
    section.registry_version = regenerated.registry_version
    db.delete(regenerated)
    db.flush()

    # Re-validate + re-render the folded section in place.
    violations = validate_section(
        db, matter=matter, planned=planned, body_tokenized=section.body_tokenized
    )
    section.validation = (
        SectionValidation.PASSED.value
        if not violations
        else SectionValidation.SURFACED_FAILED.value
    )
    render_section(db, matter=matter, section=section)

    finding.status = FindingStatus.REGENERATED.value
    db.add(section)
    db.add(finding)
    db.commit()
    return section, finding


def re_verify(
    db: Session,
    client: MeteredLLMClient | None,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    findings_scope: str | None = None,
) -> list[ComplianceFinding]:
    """Re-run the checks after a fix; flip resolved findings to ``re_verified``, add new ones.

    ALWAYS runs the deterministic pass; runs the judge additionally when a ``client`` is given
    (re-grading the regenerated sections). Lifecycle:

    * a currently-``PATCHED`` / ``REGENERATED`` finding whose condition no longer reproduces flips
      to ``RE_VERIFIED``;
    * a fixed finding whose condition STILL reproduces stays as-is (its status is left — the fix
      did not take);
    * a NEW finding (a fresh orphan a patch introduced, say) is created ``OPEN`` and persisted.

    ``findings_scope`` (a section id) narrows the re-verify to one section's findings when set;
    ``None`` re-verifies the whole draft. Commits. The engine enforces "re-verify always runs after
    a patch or regen" by calling this from both fix paths' callers.
    """
    # Imported here to avoid an import cycle (engine imports corrections).
    from app.engine.compliance.engine import (
        _persist_findings,
        _reproduced_finding_keys,
    )

    reproduced = _reproduced_finding_keys(db, client, matter=matter, plan=plan, draft=draft)

    # Existing PATCHED/REGENERATED findings this call is responsible for.
    existing = list(
        db.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.draft_id == draft.id,
                ComplianceFinding.status.in_(
                    (FindingStatus.PATCHED.value, FindingStatus.REGENERATED.value)
                ),
            )
        ).scalars()
    )
    touched: list[ComplianceFinding] = []
    for finding in existing:
        if findings_scope is not None and finding.section_id != findings_scope:
            continue
        key = (finding.check_kind, finding.section_id)
        if key not in reproduced:
            finding.status = FindingStatus.RE_VERIFIED.value
            db.add(finding)
            touched.append(finding)

    # NEW findings the fix may have introduced: any reproduced key with no OPEN row yet.
    open_keys = {
        (f.check_kind, f.section_id)
        for f in db.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.draft_id == draft.id,
                ComplianceFinding.status == FindingStatus.OPEN.value,
            )
        ).scalars()
    }
    new_findings = [f for key, f in reproduced.items() if key not in open_keys]
    if findings_scope is not None:
        new_findings = [f for f in new_findings if f.section_id == findings_scope]
    _persist_findings(db, matter=matter, draft=draft, findings=new_findings)

    db.commit()
    return touched + new_findings


__all__ = [
    "PlanNotFound",
    "SectionNotFound",
    "apply_span_patch",
    "re_verify",
    "request_section_regen",
]
