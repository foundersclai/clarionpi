"""The specials ledger — a pure, order-independent rollup over billing lines.

Integer cents in, integer cents out; no floats, no ``datetime.now()``, no I/O — this is a
derived view (schema inv 2 / money_engine §4), always recomputable from the billing-line set.
Correctness is pinned by two properties (see the money tests): shuffling the input never changes
a total (permutation invariance), and the per-category subtotals sum to the grand total, column
by column.

Optional columns (``adjusted``/``paid``/``outstanding`` may be absent on a bill that only shows
a billed amount) contribute nothing to their sum — ``None`` is treated as "no data", never as a
silent zero that would distort a reconciliation. The billed column is always present.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.money.types import Cents


class BillingLineLike(Protocol):
    """The billing-line shape the rollup reads — satisfied by the ORM row and test doubles.

    Only the money columns and the category are needed; anything with these attributes rolls up
    (ORM ``BillingLine``, a lightweight dataclass in a test, etc.).
    """

    billed_cents: int
    adjusted_cents: int | None
    paid_cents: int | None
    outstanding_cents: int | None
    category: str


@dataclass(frozen=True)
class LedgerColumns:
    """The four money columns for one category or the grand total (integer cents)."""

    billed_cents: Cents = 0
    adjusted_cents: Cents = 0
    paid_cents: Cents = 0
    outstanding_cents: Cents = 0


@dataclass(frozen=True)
class LedgerSummary:
    """A computed ledger: per-category columns plus the grand total across all lines."""

    by_category: dict[str, LedgerColumns] = field(default_factory=dict)
    grand_total: LedgerColumns = field(default_factory=LedgerColumns)


class _MutableColumns:
    """Accumulator for one category's four columns; frozen into ``LedgerColumns`` at the end."""

    __slots__ = ("billed", "adjusted", "paid", "outstanding")

    def __init__(self) -> None:
        self.billed = 0
        self.adjusted = 0
        self.paid = 0
        self.outstanding = 0

    def add(self, line: BillingLineLike) -> None:
        self.billed += line.billed_cents
        self.adjusted += line.adjusted_cents or 0
        self.paid += line.paid_cents or 0
        self.outstanding += line.outstanding_cents or 0

    def freeze(self) -> LedgerColumns:
        return LedgerColumns(
            billed_cents=self.billed,
            adjusted_cents=self.adjusted,
            paid_cents=self.paid,
            outstanding_cents=self.outstanding,
        )


def rollup(lines: Sequence[BillingLineLike]) -> LedgerSummary:
    """Roll billing lines up into per-category subtotals and a grand total.

    Pure and order-independent: the result is a function of the multiset of lines only. Category
    keys are the lines' ``category`` values (``LedgerCategory`` members on real data).
    """
    per_category: dict[str, _MutableColumns] = defaultdict(_MutableColumns)
    grand = _MutableColumns()
    for line in lines:
        per_category[line.category].add(line)
        grand.add(line)
    return LedgerSummary(
        by_category={category: cols.freeze() for category, cols in per_category.items()},
        grand_total=grand.freeze(),
    )
