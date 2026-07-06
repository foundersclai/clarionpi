"""Currency parse/format edge cases — strict, integer-only, no float drift."""

from __future__ import annotations

import pytest

from app.money.types import MoneyParseError, cents_to_display, dollars_str_to_cents


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.56", 123456),
        ("0.01", 1),
        ("0.10", 10),
        ("0", 0),
        ("0.00", 0),
        ("1000000", 100000000),
        ("1,000,000.00", 100000000),
        ("42", 4200),
        ("$5.00", 500),  # a leading '$' is tolerated so display output round-trips
        ("$1,234.56", 123456),
    ],
)
def test_parse_valid(text: str, expected: int) -> None:
    assert dollars_str_to_cents(text) == expected


@pytest.mark.parametrize("text", ["1.234", "1.2", "abc", "", "1.", "1,23.00", "1.2.3", "5$"])
def test_parse_rejects_malformed(text: str) -> None:
    with pytest.raises(MoneyParseError):
        dollars_str_to_cents(text)


def test_parse_rejects_negative_by_default() -> None:
    with pytest.raises(MoneyParseError):
        dollars_str_to_cents("-5.00")


def test_parse_allows_negative_when_opted_in() -> None:
    assert dollars_str_to_cents("-5.00", allow_negative=True) == -500


def test_parse_rejects_float_argument() -> None:
    with pytest.raises(MoneyParseError):
        dollars_str_to_cents(1.23)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (123456, "$1,234.56"),
        (1, "$0.01"),
        (0, "$0.00"),
        (100000000, "$1,000,000.00"),
        (-500, "-$5.00"),
    ],
)
def test_display(cents: int, expected: str) -> None:
    assert cents_to_display(cents) == expected


@pytest.mark.parametrize("cents", [0, 1, 10, 99, 100, 123456, 100000000])
def test_cents_display_roundtrip_is_identity(cents: int) -> None:
    # cents -> display -> parse -> cents, exact (no float drift anywhere).
    assert dollars_str_to_cents(cents_to_display(cents)) == cents
