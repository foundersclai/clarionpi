"""The AZ pack's billed-vs-paid block + the loader accessor's documented default."""

from __future__ import annotations

import pytest

from app.models.enums import RuleVerifyStatus
from app.rules.errors import UnsupportedJurisdiction
from app.rules.loader import RulePack, load_pack


def test_az_pack_parses_billed_vs_paid_block() -> None:
    pack = load_pack("AZ")
    assert pack.billed_vs_paid is not None
    assert pack.billed_vs_paid.basis == "billed"
    assert pack.billed_vs_paid.verify_status is RuleVerifyStatus.UNVERIFIED
    assert pack.billed_vs_paid.source  # non-empty cite
    assert "Lopez" in pack.billed_vs_paid.source


def test_accessor_returns_pack_basis() -> None:
    assert load_pack("AZ").billed_vs_paid_basis == "billed"


def test_accessor_defaults_to_billed_when_block_absent() -> None:
    # A pack constructed without the optional block falls back to the documented conservative
    # default (AZ v1 = billed).
    bare = RulePack(pack="X", version="0.0.0")
    assert bare.billed_vs_paid is None
    assert bare.billed_vs_paid_basis == "billed"


def test_unknown_jurisdiction_still_rejected() -> None:
    with pytest.raises(UnsupportedJurisdiction):
        load_pack("CA")
