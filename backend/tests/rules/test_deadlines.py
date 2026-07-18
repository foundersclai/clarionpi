"""AZ rule-pack loading + deadline computation, and the typed non-AZ refusal.

WD-1: ``compute_deadline_candidates`` takes the intake ``public_entity_involved`` answer and
suppresses the public-entity notice-of-claim candidate on an explicit ``NO`` — while keeping it
for ``YES`` and (fail-safe) ``UNKNOWN``. Only ``NO`` is reachable through the eligibility-gated
create route, so the ``YES``/``UNKNOWN`` behavior is exercised here at the unit level.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.models.enums import ClaimType, DeadlineKind, IntakeFlagAnswer, RuleVerifyStatus
from app.rules.deadlines import compute_deadline_candidates
from app.rules.errors import UnsupportedJurisdiction
from app.rules.loader import load_pack

_INCIDENT = dt.date(2026, 1, 15)

_ANSWERS = [IntakeFlagAnswer.NO, IntakeFlagAnswer.YES, IntakeFlagAnswer.UNKNOWN]
_ANSWER_IDS = ["no", "yes", "unknown"]


def test_az_pack_loads_as_unaudited_stub() -> None:
    pack = load_pack("AZ")
    assert pack.pack == "AZ"
    assert pack.version == "0.1.0"
    assert pack.audited is False
    assert len(pack.deadline_rules) == 2


def test_az_pack_loads_case_insensitively() -> None:
    assert load_pack("az").pack == "AZ"


def test_candidates_for_mva_incident() -> None:
    pack = load_pack("AZ")
    # public_entity_involved=YES keeps the notice-of-claim candidate, so this test still
    # exercises the full two-candidate set (the date math is the point).
    candidates = compute_deadline_candidates(
        pack, ClaimType.MVA, _INCIDENT, IntakeFlagAnswer.YES
    )

    by_kind = {c.kind: c for c in candidates}
    assert set(by_kind) == {DeadlineKind.SOL, DeadlineKind.NOTICE_OF_CLAIM}

    # Dates hardcoded (that is the point of the test): 2y SOL and 180-day notice.
    assert by_kind[DeadlineKind.SOL].date == dt.date(2028, 1, 15)
    assert by_kind[DeadlineKind.NOTICE_OF_CLAIM].date == dt.date(2026, 7, 14)


def test_candidates_carry_cites_assumptions_and_unverified_status() -> None:
    pack = load_pack("AZ")
    candidates = compute_deadline_candidates(
        pack, ClaimType.MVA, _INCIDENT, IntakeFlagAnswer.YES
    )
    by_kind = {c.kind: c for c in candidates}

    sol = by_kind[DeadlineKind.SOL]
    assert "A.R.S. § 12-542" in sol.statute_cite
    assert sol.assumptions == ["adult plaintiff — no tolling", "date of accrual = incident date"]
    assert sol.verify_status is RuleVerifyStatus.UNVERIFIED
    assert sol.confirmed is False

    notice = by_kind[DeadlineKind.NOTICE_OF_CLAIM]
    assert "A.R.S. § 12-821.01" in notice.statute_cite
    assert notice.assumptions == ["public-entity defendant — confirm at G1"]
    assert notice.verify_status is RuleVerifyStatus.UNVERIFIED


def test_unknown_jurisdiction_raises_typed_diagnostic() -> None:
    with pytest.raises(UnsupportedJurisdiction) as excinfo:
        load_pack("CA")
    assert excinfo.value.diagnostic_kind == "jurisdiction_unsupported"
    assert excinfo.value.jurisdiction == "CA"


# --------------------------------------------------------------------------------------
# WD-1 — public-entity notice-of-claim suppression (BM-01) + required-param gate (BM-04)
# --------------------------------------------------------------------------------------


def _kinds(pack, answer: IntakeFlagAnswer) -> set[DeadlineKind]:
    return {c.kind for c in compute_deadline_candidates(pack, ClaimType.MVA, _INCIDENT, answer)}


def test_notice_of_claim_suppressed_when_public_entity_no() -> None:
    # The attorney explicitly said no public entity → the §12-821.01 notice trap is
    # inapplicable and must not be offered for confirmation at G1.
    kinds = _kinds(load_pack("AZ"), IntakeFlagAnswer.NO)
    assert DeadlineKind.SOL in kinds
    assert DeadlineKind.NOTICE_OF_CLAIM not in kinds


def test_notice_of_claim_present_when_public_entity_yes() -> None:
    assert DeadlineKind.NOTICE_OF_CLAIM in _kinds(load_pack("AZ"), IntakeFlagAnswer.YES)


def test_notice_of_claim_present_when_public_entity_unknown() -> None:
    # Fail-safe: uncertainty never drops a deadline candidate.
    assert DeadlineKind.NOTICE_OF_CLAIM in _kinds(load_pack("AZ"), IntakeFlagAnswer.UNKNOWN)


@pytest.mark.parametrize("answer", _ANSWERS, ids=_ANSWER_IDS)
def test_sol_candidate_present_regardless_of_public_entity(answer: IntakeFlagAnswer) -> None:
    # The SOL/MVA rule is claim-scoped, never public-entity-gated: it survives every answer.
    assert DeadlineKind.SOL in _kinds(load_pack("AZ"), answer)


@pytest.mark.parametrize("answer", _ANSWERS, ids=_ANSWER_IDS)
def test_mva_candidate_set_never_empty(answer: IntakeFlagAnswer) -> None:
    # Suppression must never empty the set (would flip G1's non-empty invariant): SOL always present.
    candidates = compute_deadline_candidates(load_pack("AZ"), ClaimType.MVA, _INCIDENT, answer)
    assert len(candidates) >= 1


@pytest.mark.parametrize(
    ("answer", "kind", "expected_date", "expected_cite"),
    [
        (IntakeFlagAnswer.NO, DeadlineKind.SOL, dt.date(2028, 1, 15), "A.R.S. § 12-542"),
        (
            IntakeFlagAnswer.YES,
            DeadlineKind.NOTICE_OF_CLAIM,
            dt.date(2026, 7, 14),
            "A.R.S. § 12-821.01",
        ),
    ],
    ids=["sol-date-cite", "notice-date-cite-when-present"],
)
def test_surviving_candidate_fields_unchanged(
    answer: IntakeFlagAnswer,
    kind: DeadlineKind,
    expected_date: dt.date,
    expected_cite: str,
) -> None:
    # Suppression removes a whole candidate; it must not alter the fields of a surviving one.
    candidates = compute_deadline_candidates(load_pack("AZ"), ClaimType.MVA, _INCIDENT, answer)
    by_kind = {c.kind: c for c in candidates}
    survivor = by_kind[kind]
    assert survivor.date == expected_date
    assert expected_cite in survivor.statute_cite
    assert survivor.verify_status is RuleVerifyStatus.UNVERIFIED
    assert survivor.confirmed is False


def test_compute_requires_public_entity_argument() -> None:
    # The intake answer is a REQUIRED parameter — no silent default that could drop a
    # deadline for an un-threaded caller. The old 3-arg signature must not be callable.
    pack = load_pack("AZ")
    with pytest.raises(TypeError):
        compute_deadline_candidates(pack, ClaimType.MVA, _INCIDENT)  # type: ignore[call-arg]
