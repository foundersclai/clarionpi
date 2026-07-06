"""The G3 compliance pass + finding lifecycle + disposition (inv 2, 3, 6, 11, 13).

This module is the panel: it runs the two check families over a rendered
:class:`~app.models.orm.DemandDraft`, persists typed :class:`~app.models.orm.ComplianceFinding`
rows with the right severity + bucket, drives the finding lifecycle, and owns the attorney
disposition + the ``open_blocking_count`` the G3 guard reads.

Routing + severity policy (compliance §Vocabulary):

* **Bucket** — :data:`MECHANICAL_KINDS` are span-patch-routable; every other kind defaults to
  ``semantic`` (regen). The default is CONSERVATIVE (an unknown / ambiguous kind is semantic), and
  :func:`bucket_for` is exhaustively tested over every :class:`~app.models.enums.CheckKind`.
* **Hard blocks** — :data:`HARD_BLOCK_KINDS` are NEVER overridable to ship (inv 2/3/6). A hard
  block short-circuits the pass (the judge does not run — cheap-first) and cannot be dispositioned
  (:class:`HardBlockNotDisposable`). ``prose_total_mismatch`` is BLOCKING but not in the hard set:
  it is mechanically fixable/overridable, unlike an orphan.

Pass shape (:func:`run_compliance_pass`):

1. registry-drift precondition — ``draft.registry_version != matter.registry_version`` raises
   :class:`DraftRegistryDrift` (the pass refuses to run on a drifted draft; the guard's
   ``registry_version_match`` is the gate).
2. dedup — delete this draft's OPEN findings first (a re-pass re-derives them); PATCHED /
   REGENERATED / RE_VERIFIED / DISPOSITIONED rows are history, preserved.
3. deterministic pass → persist (all BLOCKING; bucket via :func:`bucket_for`).
4. ANY hard block → short-circuit (judge skipped); else the judge runs (a ``None`` / unavailable
   client is an honest ``judge_skipped`` — the deterministic findings still stand).
5. ``draft.status = IN_COMPLIANCE``; audit ``compliance_pass_completed``; commit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.llm_provider import LLMProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.tenancy import tenant_add
from app.engine.compliance.checks import build_check_context, run_deterministic_checks
from app.engine.compliance.judge import JudgeUnavailable, run_judge
from app.models.enums import (
    CheckKind,
    DraftStatus,
    FindingBucket,
    FindingDisposition,
    FindingGating,
    FindingStatus,
    UserRole,
)
from app.models.orm import ComplianceFinding, DemandDraft, DraftSection, Matter, StrategyPlan, User
from app.models.schemas import FindingActionRequest

_LOG = logging.getLogger("clarionpi.compliance.engine")

# Audit event kinds (the wiring wave is specced against these strings).
_PASS_AUDIT_KIND = "compliance_pass_completed"
_DISPOSITION_AUDIT_KIND = "compliance_finding_dispositioned"


# The mechanical-splice-routable kinds (span-patch). Every other kind is semantic (regen) by the
# conservative default. Exactly the enumerated mechanical set (compliance §Vocabulary).
MECHANICAL_KINDS: frozenset[CheckKind] = frozenset(
    {
        CheckKind.AMT_LEDGER_MISMATCH,
        CheckKind.MISSING_EXHIBIT,
        CheckKind.MISSING_STATUTORY_TERM,
        CheckKind.PROSE_TOTAL_MISMATCH,
    }
)

# The hard blocks — never overridable to ship (inv 2/3/6). A registry-version mismatch is also a
# hard block, but it is the pass PRECONDITION (DraftRegistryDrift) + the G3 guard, not a finding.
HARD_BLOCK_KINDS: frozenset[CheckKind] = frozenset(
    {
        CheckKind.ORPHAN_TOKEN,
        CheckKind.AMT_LEDGER_MISMATCH,
        CheckKind.DEAD_ANCHOR,
        CheckKind.MISSING_EXHIBIT,
        CheckKind.UNDISPOSED_ADVERSE,
    }
)


def bucket_for(kind: CheckKind) -> FindingBucket:
    """The finding bucket for a check kind: ``mechanical`` iff in :data:`MECHANICAL_KINDS`.

    Conservative default: anything not explicitly mechanical routes to ``semantic`` (regen). Total
    over :class:`CheckKind` — every member resolves to exactly one bucket.
    """
    return FindingBucket.MECHANICAL if kind in MECHANICAL_KINDS else FindingBucket.SEMANTIC


class DraftRegistryDrift(Exception):
    """The draft's registry version != the matter's — the pass refuses to run on a drifted draft.

    A bump since drafting invalidated the draft; the matter must re-confirm evidence and re-draft.
    Carries both versions for the caller/audit.
    """

    def __init__(self, *, draft_version: int, matter_version: int) -> None:
        self.draft_version = draft_version
        self.matter_version = matter_version
        super().__init__(
            f"draft registry_version {draft_version} != matter registry_version "
            f"{matter_version}; re-confirm evidence and re-draft"
        )


class HardBlockNotDisposable(Exception):
    """An attorney tried to accept/override a hard-block finding — never overridable to ship."""

    def __init__(self, *, check_kind: str) -> None:
        self.check_kind = check_kind
        super().__init__(
            f"finding kind {check_kind!r} is a hard block and cannot be dispositioned to ship "
            "(fix it via patch/regen)"
        )


class FindingDispositionForbidden(Exception):
    """A non-attorney tried to disposition a finding — disposition is attorney-only (403-style)."""

    def __init__(self, *, actual_role: str) -> None:
        self.actual_role = actual_role
        super().__init__(f"finding disposition is attorney-only; actor role is {actual_role!r}")


class DispositionActionNotSupported(Exception):
    """``patch`` / ``regen`` reached :func:`disposition_finding` — those route via corrections."""

    def __init__(self, *, action: str) -> None:
        self.action = action
        super().__init__(
            f"action {action!r} is not a disposition; patch/regen route through corrections"
        )


class DispositionReasonRequired(Exception):
    """An accept/override disposition arrived with a blank reason — both must record a rationale."""

    def __init__(self) -> None:
        super().__init__(
            "finding disposition (accept/override) requires a non-blank override_reason"
        )


@dataclass(frozen=True)
class CompliancePassOutcome:
    """The result of one compliance pass — the counts G3's payload + audit report."""

    findings_created: int
    deterministic: int
    semantic: int
    hard_blocks: int
    judge_skipped: bool
    sections_judged: int


@dataclass
class _PersistCounts:
    """Internal tally while persisting a batch of findings."""

    created: int = 0
    hard_blocks: int = 0
    kinds: list[str] = field(default_factory=list)


def _severity_for(kind: CheckKind) -> FindingGating:
    """The severity for a persisted finding — BLOCKING for every kind the panel emits at v1.

    Every deterministic finding is a block (the hard set plus ``prose_total_mismatch``), and the
    judge's semantic findings default BLOCKING too (:class:`~app.models.schemas.JudgeFinding`
    severity). ADVISORY is reserved for a later policy; the count keys off ``status`` for a
    dispositioned override, so BLOCKING-everywhere is honest at v1.
    """
    return FindingGating.BLOCKING


def _persist_findings(
    db: Session,
    *,
    matter: Matter,
    draft: DemandDraft,
    findings: Sequence[ComplianceFinding],
) -> _PersistCounts:
    """Stamp severity + bucket + firm on OPEN findings and add them (uncommitted).

    Each finding already carries ``check_kind`` / ``section_id`` / ``detail`` / anchors / span /
    ``registry_version`` from the check or judge; this applies the routing policy (severity via
    :func:`_severity_for`, bucket via :func:`bucket_for` unless already escalated) and tenants the
    row. Returns the tally (created count + hard-block count + the kinds seen).
    """
    counts = _PersistCounts()
    for finding in findings:
        kind = CheckKind(finding.check_kind)
        finding.severity = _severity_for(kind).value
        # Only default the bucket when a caller has not already set it (e.g. a runtime escalation).
        if not finding.bucket:
            finding.bucket = bucket_for(kind).value
        if not finding.status:
            finding.status = FindingStatus.OPEN.value
        tenant_add(db, finding, matter.firm_id)
        counts.created += 1
        counts.kinds.append(finding.check_kind)
        if kind in HARD_BLOCK_KINDS:
            counts.hard_blocks += 1
    return counts


def _delete_open_findings(db: Session, *, draft: DemandDraft) -> None:
    """Delete this draft's OPEN findings (a re-pass re-derives them; history rows are preserved).

    PATCHED / REGENERATED / RE_VERIFIED / DISPOSITIONED rows are the finding history and are left
    untouched — only OPEN rows (the last pass's live derivation) are cleared before a re-derive.
    """
    open_rows = list(
        db.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.draft_id == draft.id,
                ComplianceFinding.status == FindingStatus.OPEN.value,
            )
        ).scalars()
    )
    for row in open_rows:
        db.delete(row)
    db.flush()


def _plan_for_draft(db: Session, *, matter: Matter, draft: DemandDraft) -> StrategyPlan | None:
    """The :class:`StrategyPlan` the draft was drafted from (by version), or ``None``."""
    return db.execute(
        select(StrategyPlan).where(
            StrategyPlan.matter_id == matter.id,
            StrategyPlan.version == draft.strategy_plan_version,
        )
    ).scalar_one_or_none()


def _draft_sections(db: Session, *, draft: DemandDraft) -> list[DraftSection]:
    """The draft's sections in collation order (``sort_order``, then id)."""
    rows = list(db.execute(select(DraftSection).where(DraftSection.draft_id == draft.id)).scalars())
    rows.sort(key=lambda s: (s.sort_order, str(s.id)))
    return rows


def run_compliance_pass(
    db: Session,
    client: MeteredLLMClient | None,
    *,
    matter: Matter,
    draft: DemandDraft,
) -> CompliancePassOutcome:
    """Run the G3 compliance pass over ``draft`` and persist its findings. See the module doc.

    Raises :class:`DraftRegistryDrift` when the draft's registry version has drifted from the
    matter's. Otherwise: clears the draft's OPEN findings, runs the deterministic checks and
    persists them (all BLOCKING; bucket via :func:`bucket_for`), and — unless a hard block
    short-circuits it — runs the Sonnet judge and persists its semantic findings. A ``None`` client
    or a :class:`~app.engine.compliance.judge.JudgeUnavailable` outage sets ``judge_skipped`` (the
    deterministic findings still stand). Sets ``draft.status = IN_COMPLIANCE``, writes a
    ``compliance_pass_completed`` audit event, and commits.
    """
    if draft.registry_version != matter.registry_version:
        raise DraftRegistryDrift(
            draft_version=draft.registry_version, matter_version=matter.registry_version
        )

    _delete_open_findings(db, draft=draft)

    # ---- Deterministic pass ---------------------------------------------------------------
    ctx = build_check_context(db, matter=matter, draft=draft)
    deterministic = run_deterministic_checks(db, ctx)
    det_counts = _persist_findings(db, matter=matter, draft=draft, findings=deterministic)

    hard_blocks = det_counts.hard_blocks
    semantic_created = 0
    sections_judged = 0
    judge_skipped = False

    # ---- Semantic pass (cheap-first: skip on any hard block) ------------------------------
    if hard_blocks > 0:
        judge_skipped = True
        _LOG.info(
            "compliance: %d hard block(s) on draft %s; skipping the semantic judge (cheap-first)",
            hard_blocks,
            draft.id,
        )
    elif client is None:
        judge_skipped = True
        _LOG.info("compliance: no LLM client; semantic pass unavailable for draft %s", draft.id)
    else:
        plan = _plan_for_draft(db, matter=matter, draft=draft)
        sections = _draft_sections(db, draft=draft)
        if plan is None:
            judge_skipped = True
            _LOG.warning(
                "compliance: no plan v%s for draft %s; skipping the semantic judge",
                draft.strategy_plan_version,
                draft.id,
            )
        else:
            try:
                semantic = run_judge(
                    db, client, matter=matter, plan=plan, draft=draft, sections=sections
                )
            except JudgeUnavailable as exc:
                judge_skipped = True
                _LOG.warning("compliance: semantic pass unavailable (%s)", exc.reason)
            else:
                sem_counts = _persist_findings(db, matter=matter, draft=draft, findings=semantic)
                semantic_created = sem_counts.created
                sections_judged = len(sections)

    draft.status = DraftStatus.IN_COMPLIANCE.value
    db.add(draft)

    outcome = CompliancePassOutcome(
        findings_created=det_counts.created + semantic_created,
        deterministic=det_counts.created,
        semantic=semantic_created,
        hard_blocks=hard_blocks,
        judge_skipped=judge_skipped,
        sections_judged=sections_judged,
    )
    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=None,
        event_kind=_PASS_AUDIT_KIND,
        payload={
            "matter_id": str(matter.id),
            "draft_id": str(draft.id),
            "draft_version": draft.version,
            "findings_created": outcome.findings_created,
            "deterministic": outcome.deterministic,
            "semantic": outcome.semantic,
            "hard_blocks": outcome.hard_blocks,
            "judge_skipped": outcome.judge_skipped,
            "sections_judged": outcome.sections_judged,
        },
    )
    db.commit()
    return outcome


def _reproduced_finding_keys(
    db: Session,
    client: MeteredLLMClient | None,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
) -> dict[tuple[str, str], ComplianceFinding]:
    """Re-derive the currently-reproducing findings, keyed ``(check_kind, section_id)``.

    Runs the deterministic checks (always) and — when a ``client`` is given AND no hard block
    reproduces — the judge, returning a fresh (uncommitted) finding per distinct key (first wins).
    This is the re-verify probe: a key present here still fails; a fixed finding whose key is absent
    has been resolved. Findings are NOT persisted here — ``corrections.re_verify`` decides the
    lifecycle.
    """
    ctx = build_check_context(db, matter=matter, draft=draft)
    deterministic = run_deterministic_checks(db, ctx)
    by_key: dict[tuple[str, str], ComplianceFinding] = {}
    hard_block = False
    for finding in deterministic:
        key = (finding.check_kind, finding.section_id)
        by_key.setdefault(key, finding)
        if CheckKind(finding.check_kind) in HARD_BLOCK_KINDS:
            hard_block = True

    if client is not None and not hard_block:
        sections = _draft_sections(db, draft=draft)
        try:
            semantic = run_judge(
                db, client, matter=matter, plan=plan, draft=draft, sections=sections
            )
        except JudgeUnavailable:
            semantic = []
        for finding in semantic:
            key = (finding.check_kind, finding.section_id)
            by_key.setdefault(key, finding)
    return by_key


def disposition_finding(
    db: Session,
    *,
    user: User,
    finding: ComplianceFinding,
    request: FindingActionRequest,
) -> ComplianceFinding:
    """Accept or override a single semantic finding — **attorney-only**, with a logged rationale.

    * ``patch`` / ``regen`` -> :class:`DispositionActionNotSupported` (those route through
      corrections).
    * a hard-block kind (:data:`HARD_BLOCK_KINDS`) -> :class:`HardBlockNotDisposable` (never
      overridable to ship).
    * a non-attorney actor -> :class:`FindingDispositionForbidden` (403-style).
    * ``accept`` / ``override`` -> require a non-blank ``override_reason``
      (:class:`DispositionReasonRequired`); set ``status = DISPOSITIONED``, ``disposition`` to
      ``accept`` / ``override``, ``disposition_by``, and ``override_reason``. An OVERRIDE marks the
      finding as an audited proceed-past (its effect: it drops out of ``open_blocking_count`` via
      the DISPOSITIONED status — the ADVISORY effect the contract describes). Writes a
      ``compliance_finding_dispositioned`` audit event and commits.
    """
    if request.action in ("patch", "regen"):
        raise DispositionActionNotSupported(action=request.action)

    kind = CheckKind(finding.check_kind)
    if kind in HARD_BLOCK_KINDS:
        raise HardBlockNotDisposable(check_kind=finding.check_kind)

    if user.role != UserRole.ATTORNEY.value:
        raise FindingDispositionForbidden(actual_role=user.role)

    if not (request.override_reason or "").strip():
        raise DispositionReasonRequired()

    disposition = (
        FindingDisposition.ACCEPT if request.action == "accept" else FindingDisposition.OVERRIDE
    )
    finding.status = FindingStatus.DISPOSITIONED.value
    finding.disposition = disposition.value
    finding.disposition_by = user.id
    finding.override_reason = request.override_reason
    db.add(finding)
    record_event(
        db,
        firm_id=finding.firm_id,
        actor_id=user.id,
        event_kind=_DISPOSITION_AUDIT_KIND,
        payload={
            "finding_id": str(finding.id),
            "draft_id": str(finding.draft_id),
            "check_kind": finding.check_kind,
            "disposition": disposition.value,
            "override_reason": request.override_reason,
        },
    )
    db.commit()
    return finding


def open_blocking_count(db: Session, *, matter: Matter, draft: DemandDraft) -> int:
    """Open blocking findings on ``draft`` — feeds the G3 ``no_blocking_findings`` guard.

    Counts findings with ``severity == blocking`` whose ``status`` is NOT in
    ``{re_verified, dispositioned}`` — a re-verified fix and an attorney-dispositioned finding both
    clear the block. This is the exact number the guard context reads for the latest draft.
    """
    return db.execute(
        select(func.count())
        .select_from(ComplianceFinding)
        .where(
            ComplianceFinding.draft_id == draft.id,
            ComplianceFinding.firm_id == matter.firm_id,
            ComplianceFinding.severity == FindingGating.BLOCKING.value,
            ComplianceFinding.status.notin_(
                (FindingStatus.RE_VERIFIED.value, FindingStatus.DISPOSITIONED.value)
            ),
        )
    ).scalar_one()


def latest_draft(db: Session, *, matter: Matter) -> DemandDraft | None:
    """The matter's highest-version :class:`DemandDraft`, or ``None`` (no draft yet).

    The G3 guard reads blocking findings over this draft; a matter with no draft has zero blocking
    findings (nothing has been drafted to find fault with).
    """
    drafts = list(
        db.execute(select(DemandDraft).where(DemandDraft.matter_id == matter.id)).scalars()
    )
    if not drafts:
        return None
    return max(drafts, key=lambda d: d.version)


def compliance_post_draft_hook(
    provider: LLMProvider,
) -> Callable[[Session, Matter, DemandDraft], None]:
    """The ``post_draft`` factory the wiring wave hands ``run_demand_generation``.

    Returns a callable that, given the session / matter / freshly-VALIDATED draft, builds a
    per-call :class:`~app.core.llm_telemetry.MeteredLLMClient` from ``provider`` and runs the
    compliance pass. It swallows NOTHING structural — a :class:`DraftRegistryDrift` propagates (the
    draft should not have validated on a drifted matter); only a provider outage inside the judge is
    already an honest ``judge_skipped`` inside the pass, so the hook does not special-case it.
    """

    def _hook(db: Session, matter: Matter, draft: DemandDraft) -> None:
        client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
        run_compliance_pass(db, client, matter=matter, draft=draft)

    return _hook


__all__ = [
    "HARD_BLOCK_KINDS",
    "MECHANICAL_KINDS",
    "CompliancePassOutcome",
    "DispositionActionNotSupported",
    "DispositionReasonRequired",
    "DraftRegistryDrift",
    "FindingDispositionForbidden",
    "HardBlockNotDisposable",
    "bucket_for",
    "compliance_post_draft_hook",
    "disposition_finding",
    "latest_draft",
    "open_blocking_count",
    "run_compliance_pass",
]
