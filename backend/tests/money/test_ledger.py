"""Specials-ledger rollup: fixed example + hypothesis properties (permutation + category sums)."""

from __future__ import annotations

import random
from dataclasses import dataclass

from hypothesis import given
from hypothesis import strategies as st

from app.models.enums import LedgerCategory
from app.money.ledger import LedgerColumns, rollup

_CATEGORIES = [c.value for c in LedgerCategory]


@dataclass
class FakeLine:
    """A billing-line double satisfying ``BillingLineLike`` (money columns + category)."""

    billed_cents: int
    adjusted_cents: int | None
    paid_cents: int | None
    outstanding_cents: int | None
    category: str


def test_fixed_example_rolls_up_by_category_and_grand_total() -> None:
    lines = [
        FakeLine(10_000, 2_000, 6_000, 2_000, LedgerCategory.ER.value),
        FakeLine(5_000, None, 5_000, 0, LedgerCategory.ER.value),
        FakeLine(30_000, 10_000, 15_000, 5_000, LedgerCategory.IMAGING.value),
    ]
    summary = rollup(lines)

    assert summary.by_category[LedgerCategory.ER.value] == LedgerColumns(
        billed_cents=15_000, adjusted_cents=2_000, paid_cents=11_000, outstanding_cents=2_000
    )
    assert summary.by_category[LedgerCategory.IMAGING.value] == LedgerColumns(
        billed_cents=30_000, adjusted_cents=10_000, paid_cents=15_000, outstanding_cents=5_000
    )
    assert summary.grand_total == LedgerColumns(
        billed_cents=45_000, adjusted_cents=12_000, paid_cents=26_000, outstanding_cents=7_000
    )


def test_empty_input_is_all_zero() -> None:
    summary = rollup([])
    assert summary.by_category == {}
    assert summary.grand_total == LedgerColumns()


_amount = st.integers(min_value=0, max_value=10_000_000)
_opt_amount = st.none() | _amount
_line = st.builds(
    FakeLine,
    billed_cents=_amount,
    adjusted_cents=_opt_amount,
    paid_cents=_opt_amount,
    outstanding_cents=_opt_amount,
    category=st.sampled_from(_CATEGORIES),
)


@given(lines=st.lists(_line, max_size=40))
def test_permutation_invariance(lines: list[FakeLine]) -> None:
    shuffled = list(lines)
    random.Random(1234).shuffle(shuffled)
    assert rollup(lines) == rollup(shuffled)


@given(lines=st.lists(_line, max_size=40))
def test_category_subtotals_sum_to_grand_total(lines: list[FakeLine]) -> None:
    summary = rollup(lines)
    for column in ("billed_cents", "adjusted_cents", "paid_cents", "outstanding_cents"):
        category_sum = sum(getattr(cols, column) for cols in summary.by_category.values())
        assert category_sum == getattr(summary.grand_total, column)
