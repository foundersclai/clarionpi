"""Per-matter AI spend cap (invariant 12) — caps ON by default, wired from day 1.

This module owns the budget row lifecycle and the accounting the metered client calls:

* :func:`load_or_create_budget` — fetch the matter's :class:`~app.models.orm.MatterBudget`,
  creating it at the configured default cap on first use.
* :func:`assert_within_budget` — raise :class:`BudgetExceededError` when the matter is already
  at/over cap, *before* any provider call (the metered client checks this first, so a capped
  matter never reaches the model).
* :func:`commit_spend` — add actual cost to ``spent_cents`` and return whether this crossed the
  80% warning threshold (an idempotent latch on ``budget.warned``).

The TM lesson made structural: there is no unmetered path and no post-hoc reconciliation — the
ledger is written on every attempt (see :mod:`app.core.llm_telemetry`) and the cap is checked
up front.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.orm import MatterBudget

# Fraction of the cap at which a one-shot ``budget_warning`` fires.
_WARN_FRACTION_NUM = 80
_WARN_FRACTION_DEN = 100


class BudgetExceededError(Exception):
    """Raised when a matter's spend has reached its cap; the call is refused, not stalled."""

    def __init__(self, *, matter_id: uuid.UUID, spent_cents: int, cap_cents: int) -> None:
        self.matter_id = matter_id
        self.spent_cents = spent_cents
        self.cap_cents = cap_cents
        super().__init__(
            f"matter {matter_id} budget exhausted: spent {spent_cents} >= cap {cap_cents} cents"
        )


def load_or_create_budget(
    session: Session, *, firm_id: uuid.UUID, matter_id: uuid.UUID
) -> MatterBudget:
    """Return the matter's budget row, creating it at the default cap on first use.

    The default cap comes from :class:`~app.core.config.Settings` — caps are ON by default, so
    there is always a budget to check against.
    """
    budget = session.query(MatterBudget).filter(MatterBudget.matter_id == matter_id).one_or_none()
    if budget is None:
        budget = MatterBudget(
            firm_id=firm_id,
            matter_id=matter_id,
            cap_cents=get_settings().matter_budget_default_cents,
            spent_cents=0,
            warned=False,
        )
        session.add(budget)
        session.flush()
    return budget


def assert_within_budget(budget: MatterBudget) -> None:
    """Raise :class:`BudgetExceededError` if the matter is already at or over its cap.

    Called *before* the provider so an exhausted matter never issues a model call.
    """
    if budget.spent_cents >= budget.cap_cents:
        raise BudgetExceededError(
            matter_id=budget.matter_id,
            spent_cents=budget.spent_cents,
            cap_cents=budget.cap_cents,
        )


def _crosses_warn_threshold(cap_cents: int, before_cents: int, after_cents: int) -> bool:
    """True iff spend moved from below the 80% line to at/above it (integer math, no floats)."""
    threshold_num = cap_cents * _WARN_FRACTION_NUM
    before_num = before_cents * _WARN_FRACTION_DEN
    after_num = after_cents * _WARN_FRACTION_DEN
    return before_num < threshold_num <= after_num


def commit_spend(budget: MatterBudget, *, cost_cents: int) -> bool:
    """Add ``cost_cents`` to the budget and report whether this crossed the 80% warn line.

    The warning is an idempotent latch: it returns ``True`` only on the *first* crossing (when
    ``budget.warned`` was still ``False``), and sets ``budget.warned`` so a subsequent call over
    the line does not re-warn.
    """
    before = budget.spent_cents
    after = before + cost_cents
    budget.spent_cents = after
    if not budget.warned and _crosses_warn_threshold(budget.cap_cents, before, after):
        budget.warned = True
        return True
    return False
