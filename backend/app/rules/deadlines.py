"""Pure deadline computation — rule pack + facts → :class:`DeadlineCandidate` list.

Invariant 3 + 4: date math is pure code (no ``datetime.now()`` anywhere — determinism), and the
output is a list of *candidates* carrying their statute cite, assumptions, and verify status for
attorney confirmation at G1, never a lone silent date. Year math uses
:class:`dateutil.relativedelta.relativedelta` (calendar-correct across leap years); day math uses
:class:`datetime.timedelta`.
"""

from __future__ import annotations

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from app.models.enums import ClaimType, DeadlineKind, IntakeFlagAnswer
from app.models.schemas import DeadlineCandidate
from app.rules.loader import RulePack, RuleRow


def _candidate_date(rule: RuleRow, incident_date: date) -> date:
    """Compute a rule's deadline date from the incident date (pure; kind-driven period)."""
    if rule.kind is DeadlineKind.SOL:
        assert rule.years is not None  # loader guarantees this for sol rows
        return incident_date + relativedelta(years=rule.years)
    assert rule.days is not None  # loader guarantees this for notice_of_claim rows
    return incident_date + timedelta(days=rule.days)


def _rule_applies(
    rule: RuleRow, claim_type: ClaimType, public_entity_involved: IntakeFlagAnswer
) -> bool:
    """Whether a rule contributes a candidate for this matter.

    The public-entity notice-of-claim rule (``kind`` = ``NOTICE_OF_CLAIM``; A.R.S. § 12-821.01 in
    AZ v1) is gated on the intake ``public_entity_involved`` answer. It is SUPPRESSED only on an
    explicit ``NO`` — the attorney said no public entity is involved, so the 180-day notice trap
    is inapplicable and must not clutter G1 — and INCLUDED on ``YES`` and, fail-safe, ``UNKNOWN``:
    uncertainty must never silently drop a deadline the attorney would otherwise confirm.

    Every other rule keeps its prior applicability: a ``claim_type``-scoped rule (the SOL) fires
    only for that claim type; a hypothetical un-scoped non-notice rule applies to every matter.

    WD-1 keys the gate on ``kind`` in code rather than a pack field so the pack bytes (and its
    provenance fingerprint) are unchanged; the pack's ``applies_when`` documents the coupling. A
    v2 pack that needs a non-public-entity notice-of-claim rule, or a public-entity SOL rule, must
    move this applicability key into the pack.
    """
    if rule.kind is DeadlineKind.NOTICE_OF_CLAIM:
        return public_entity_involved is not IntakeFlagAnswer.NO
    if rule.claim_type is None:
        return True
    return rule.claim_type == claim_type.value


def compute_deadline_candidates(
    pack: RulePack,
    claim_type: ClaimType,
    incident_date: date,
    public_entity_involved: IntakeFlagAnswer,
) -> list[DeadlineCandidate]:
    """Return the deadline candidates for a matter — deterministic, attorney-confirmed later.

    Every candidate carries the pack row's ``statute_cite``, ``assumptions``, and
    ``verify_status``, and is ``confirmed=False`` until the attorney signs off at G1. The
    ``public_entity_involved`` intake answer is REQUIRED (no default): it gates the public-entity
    notice-of-claim candidate through :func:`_rule_applies`, so an un-threaded caller fails loudly
    rather than silently keeping or dropping a legally material deadline.
    """
    candidates: list[DeadlineCandidate] = []
    for rule in pack.deadline_rules:
        if not _rule_applies(rule, claim_type, public_entity_involved):
            continue
        candidates.append(
            DeadlineCandidate(
                kind=rule.kind,
                date=_candidate_date(rule, incident_date),
                statute_cite=rule.statute_cite,
                assumptions=list(rule.assumptions),
                verify_status=rule.verify_status,
                confirmed=False,
            )
        )
    return candidates
