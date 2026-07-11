"""Rule-pack authority + fingerprint + pin verification (BUS-02).

A boolean alone never makes a pack authoritative: ``audited: true`` demands counsel-audit
provenance at validation, and ``is_authoritative`` additionally demands every legal input
drafting/package assembly consumes be ``verified``. The fingerprint is the provenance pin —
deterministic, and changed by every authority-relevant mutation.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.rules.errors import RulePackChanged, RulePackUnaudited, RulePackUnpinned
from app.rules.loader import RulePack, load_pack, load_pack_for_pin


def _verified_pack_data(**overrides: Any) -> dict[str, Any]:
    """A fully verified, fully audited pack — the authoritative baseline for these tests."""
    data: dict[str, Any] = {
        "pack": "AZ",
        "version": "1.0.0",
        "audited": True,
        "audited_by": "Legal Cofounder, Esq.",
        "audited_at": dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        "audit_reference": "audit-memo-2026-07-01",
        "deadline_rules": [
            {
                "kind": "sol",
                "claim_type": "mva",
                "years": 2,
                "statute_cite": "A.R.S. § 12-542",
                "verify_status": "verified",
            }
        ],
        "billed_vs_paid": {
            "basis": "billed",
            "source": "Lopez v. Safeway Stores",
            "verify_status": "verified",
        },
        "letter_structure": {
            "source": "ClarionPI drafting standard v1",
            "verify_status": "verified",
            "sections": [
                {
                    "section_id": "intro",
                    "purpose": "Introduce.",
                    "max_words": 100,
                    "required_token_kinds": [],
                }
            ],
        },
    }
    data.update(overrides)
    return data


def test_shipped_az_pack_is_valid_but_not_authoritative() -> None:
    pack = load_pack("AZ")
    assert pack.audited is False
    assert pack.is_authoritative is False  # unaudited stub: usable, never authoritative


def test_fully_verified_audited_pack_is_authoritative() -> None:
    pack = RulePack.model_validate(_verified_pack_data())
    assert pack.is_authoritative is True


@pytest.mark.parametrize(
    "missing",
    [
        {"audited_by": None},
        {"audited_by": "   "},
        {"audited_at": None},
        {"audited_at": dt.datetime(2026, 7, 1)},  # naive — tz-awareness is required
        {"audit_reference": None},
    ],
)
def test_audited_true_with_missing_provenance_is_rejected(missing: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="audited: true requires"):
        RulePack.model_validate(_verified_pack_data(**missing))


@pytest.mark.parametrize(
    "downgrade",
    [
        {"deadline_rules": []},  # no deadline rules at all
        {"billed_vs_paid": None},  # conservative fallback cannot back a production package
        {"letter_structure": None},  # the drafted sections derive from this skeleton
    ],
)
def test_missing_legal_inputs_stay_non_authoritative(downgrade: dict[str, Any]) -> None:
    pack = RulePack.model_validate(_verified_pack_data(**downgrade))
    assert pack.is_authoritative is False


def test_any_unverified_row_stays_non_authoritative() -> None:
    data = _verified_pack_data()
    data["deadline_rules"][0]["verify_status"] = "unverified"
    assert RulePack.model_validate(data).is_authoritative is False
    data = _verified_pack_data()
    data["billed_vs_paid"]["verify_status"] = "unverified"
    assert RulePack.model_validate(data).is_authoritative is False
    data = _verified_pack_data()
    data["letter_structure"]["verify_status"] = "unverified"
    assert RulePack.model_validate(data).is_authoritative is False


def test_fingerprint_is_deterministic_and_tracks_authority_relevant_mutations() -> None:
    base = RulePack.model_validate(_verified_pack_data()).fingerprint
    again = RulePack.model_validate(_verified_pack_data()).fingerprint
    assert base == again  # deterministic across constructions

    mutations: list[dict[str, Any]] = [
        {"audited_by": "Someone Else"},  # audit metadata
        {"version": "1.0.1"},  # version string
    ]
    for mutation in mutations:
        assert RulePack.model_validate(_verified_pack_data(**mutation)).fingerprint != base

    # Verification-status and legal-input mutations change it too.
    data = _verified_pack_data()
    data["deadline_rules"][0]["verify_status"] = "unverified"
    assert RulePack.model_validate(data).fingerprint != base
    data = _verified_pack_data()
    data["deadline_rules"][0]["years"] = 3  # behavior-affecting legal input
    assert RulePack.model_validate(data).fingerprint != base
    data = _verified_pack_data()
    data["billed_vs_paid"]["basis"] = "paid"
    assert RulePack.model_validate(data).fingerprint != base


# ---------------------------------------------------------------------------------------
# load_pack_for_pin
# ---------------------------------------------------------------------------------------


def test_matching_pin_passes_without_authority_requirement() -> None:
    current = load_pack("AZ")
    pack = load_pack_for_pin(
        "AZ", current.version, current.fingerprint, require_authoritative=False
    )
    assert pack.version == current.version


def test_version_or_fingerprint_drift_raises_changed() -> None:
    current = load_pack("AZ")
    with pytest.raises(RulePackChanged):
        load_pack_for_pin("AZ", "9.9.9", current.fingerprint, require_authoritative=False)
    with pytest.raises(RulePackChanged):
        load_pack_for_pin("AZ", current.version, "0" * 64, require_authoritative=False)


def test_unpinned_legacy_matter_passes_only_when_authority_not_required() -> None:
    pack = load_pack_for_pin("AZ", None, None, require_authoritative=False)
    assert pack.pack == "AZ"
    with pytest.raises(RulePackUnpinned):
        load_pack_for_pin("AZ", None, None, require_authoritative=True)


def test_pinned_unaudited_pack_refuses_authority_requirement() -> None:
    current = load_pack("AZ")  # the shipped stub is unaudited
    with pytest.raises(RulePackUnaudited) as exc:
        load_pack_for_pin("AZ", current.version, current.fingerprint, require_authoritative=True)
    assert exc.value.jurisdiction == "AZ"
    assert exc.value.version == current.version
