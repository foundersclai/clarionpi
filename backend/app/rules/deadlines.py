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

from app.models.enums import ClaimType, DeadlineKind
from app.models.schemas import DeadlineCandidate
from app.rules.loader import RulePack, RuleRow


def _candidate_date(rule: RuleRow, incident_date: date) -> date:
    """Compute a rule's deadline date from the incident date (pure; kind-driven period)."""
    if rule.kind is DeadlineKind.SOL:
        assert rule.years is not None  # loader guarantees this for sol rows
        return incident_date + relativedelta(years=rule.years)
    assert rule.days is not None  # loader guarantees this for notice_of_claim rows
    return incident_date + timedelta(days=rule.days)


def _rule_applies(rule: RuleRow, claim_type: ClaimType) -> bool:
    """Whether a rule contributes a candidate for this claim type.

    A rule scoped to a ``claim_type`` only fires for that claim type; a rule with no
    ``claim_type`` (e.g. the public-entity notice-of-claim trap, gated by an informational
    ``applies_when`` the attorney confirms) applies to every matter.
    """
    if rule.claim_type is None:
        return True
    return rule.claim_type == claim_type.value


def compute_deadline_candidates(
    pack: RulePack, claim_type: ClaimType, incident_date: date
) -> list[DeadlineCandidate]:
    """Return the deadline candidates for a matter — deterministic, attorney-confirmed later.

    Every candidate carries the pack row's ``statute_cite``, ``assumptions``, and
    ``verify_status``, and is ``confirmed=False`` until the attorney signs off at G1.
    """
    candidates: list[DeadlineCandidate] = []
    for rule in pack.deadline_rules:
        if not _rule_applies(rule, claim_type):
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
