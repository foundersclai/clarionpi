"""AZ rule-pack loading + deadline computation, and the typed non-AZ refusal."""

from __future__ import annotations

import datetime as dt

import pytest

from app.models.enums import ClaimType, DeadlineKind, RuleVerifyStatus
from app.rules.deadlines import compute_deadline_candidates
from app.rules.errors import UnsupportedJurisdiction
from app.rules.loader import load_pack

_INCIDENT = dt.date(2026, 1, 15)


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
    candidates = compute_deadline_candidates(pack, ClaimType.MVA, _INCIDENT)

    by_kind = {c.kind: c for c in candidates}
    assert set(by_kind) == {DeadlineKind.SOL, DeadlineKind.NOTICE_OF_CLAIM}

    # Dates hardcoded (that is the point of the test): 2y SOL and 180-day notice.
    assert by_kind[DeadlineKind.SOL].date == dt.date(2028, 1, 15)
    assert by_kind[DeadlineKind.NOTICE_OF_CLAIM].date == dt.date(2026, 7, 14)


def test_candidates_carry_cites_assumptions_and_unverified_status() -> None:
    pack = load_pack("AZ")
    candidates = compute_deadline_candidates(pack, ClaimType.MVA, _INCIDENT)
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
