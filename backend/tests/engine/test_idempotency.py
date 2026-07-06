"""Idempotency-key derivation + validation tests."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.engine.orchestrator.errors import InvalidIdempotencyKey
from app.engine.orchestrator.idempotency import derive_key, validate_client_key
from app.models.enums import GateState

MATTER_A = UUID("11111111-1111-1111-1111-111111111111")
MATTER_B = UUID("22222222-2222-2222-2222-222222222222")
CLIENT_KEY = "submit-abc_123.v1"


def test_derive_key_is_deterministic() -> None:
    k1 = derive_key(MATTER_A, GateState.PLAN_REVIEW, CLIENT_KEY)
    k2 = derive_key(MATTER_A, GateState.PLAN_REVIEW, CLIENT_KEY)
    assert k1 == k2
    # sha256 hex digest shape.
    assert len(k1) == 64
    assert all(c in "0123456789abcdef" for c in k1)


def test_derive_key_distinct_across_gates() -> None:
    k_plan = derive_key(MATTER_A, GateState.PLAN_REVIEW, CLIENT_KEY)
    k_compliance = derive_key(MATTER_A, GateState.COMPLIANCE_REVIEW, CLIENT_KEY)
    assert k_plan != k_compliance


def test_derive_key_distinct_across_matters() -> None:
    k_a = derive_key(MATTER_A, GateState.PLAN_REVIEW, CLIENT_KEY)
    k_b = derive_key(MATTER_B, GateState.PLAN_REVIEW, CLIENT_KEY)
    assert k_a != k_b


def test_derive_key_distinct_across_client_keys() -> None:
    k1 = derive_key(MATTER_A, GateState.PLAN_REVIEW, "key-one_1")
    k2 = derive_key(MATTER_A, GateState.PLAN_REVIEW, "key-two_2")
    assert k1 != k2


def test_derive_key_rejects_bad_client_key() -> None:
    with pytest.raises(InvalidIdempotencyKey):
        derive_key(MATTER_A, GateState.PLAN_REVIEW, "has spaces")


# --- validate_client_key ---------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "abcdefgh",  # exactly 8 (min)
        "a" * 128,  # exactly 128 (max)
        "Submit_Gate-2.5.v1",
        "0123456789",
    ],
)
def test_validate_accepts_well_formed_keys(key: str) -> None:
    assert validate_client_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",  # empty
        "short7c",  # 7 chars, below min
        "a" * 129,  # 129 chars, above max
    ],
)
def test_validate_rejects_bad_lengths(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey) as excinfo:
        validate_client_key(key)
    assert "length" in excinfo.value.reason
    assert excinfo.value.key == key


@pytest.mark.parametrize(
    "key",
    [
        "has spaces here",
        "bad/slash/key",
        "colon:delimited",
        "emoji-\U0001f600-key",
        "new\nline\nkey",
        "unit\x1fseparator",
    ],
)
def test_validate_rejects_bad_chars(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey) as excinfo:
        validate_client_key(key)
    assert "characters" in excinfo.value.reason
