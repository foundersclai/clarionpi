"""The G2a billing-line edit service — write SOURCE rows, return the recomputed derived ledger.

The ledger grid edits only :class:`~app.models.orm.BillingLine` SOURCE rows; the specials ledger
itself is a *derived view* (inv 10 / money_engine §4), never written. This service applies a batch
of source-row edits and then hands back a freshly recomputed ``SpecialsLedger`` so the FE re-renders
the grid off one round-trip and never computes a total itself.

Money discipline (the AGENTS boundary): dollar strings parse to integer cents ONLY through
:func:`app.money.types.dollars_str_to_cents`. A malformed string is a typed
:class:`~app.money.types.MoneyParseError` the caller maps to a 422 — a bad value is never stored as
a guess. Category recategorization accepts only the closed :class:`~app.models.enums.LedgerCategory`
taxonomy, which the schema (:class:`~app.models.schemas.BillingLineEdit`) has already validated into
an enum, so a bogus category string cannot reach this layer.

The whole batch is atomic: an unknown line or a parse error anywhere in the batch aborts the entire
edit (nothing commits), so a half-applied batch is impossible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.models.orm import BillingLine, Matter
from app.models.schemas import BillingLineEdit, BillingLineEditBatch
from app.money.assemble import compute_matter_ledger
from app.money.specials import SpecialsLedger
from app.money.types import dollars_str_to_cents
from app.rules.loader import RulePack

# The source-row money columns an edit may set, mapped from the edit schema's dollar-string field
# name to the ORM row's cents column. Category is applied separately (it is an enum, not money).
_MONEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("billed", "billed_cents"),
    ("adjusted", "adjusted_cents"),
    ("paid", "paid_cents"),
    ("outstanding", "outstanding_cents"),
)


class UnknownBillingLine(Exception):
    """An edit named a billing-line id that is not on this matter (the caller maps to a 422).

    Carries the offending ``line_id`` so the refusal names it. Raised before any row is mutated
    for that edit, and since the batch is atomic no earlier edit in the batch survives it either.
    """

    def __init__(self, *, line_id: uuid.UUID) -> None:
        self.line_id = line_id
        super().__init__(f"no billing line {line_id} on this matter")


@dataclass(frozen=True)
class BillingEditOutcome:
    """The result of an applied edit batch — counts plus the recomputed derived ledger.

    ``ledger`` is recomputed AFTER the edits commit (a fresh :class:`SpecialsLedger` with a fresh
    ``line_set_hash``) — the grid-refetch payload, so the FE renders totals it never computed.
    """

    edited: int
    recategorized: int
    reparsed_money_fields: int
    ledger: SpecialsLedger


def _apply_one_edit(
    db: Session, *, matter: Matter, edit: BillingLineEdit
) -> tuple[list[str], bool]:
    """Apply one source-row edit; return (changed money field names, whether category changed).

    The line must belong to the matter (firm scoping is by the caller's session; the matter check
    is explicit) — else :class:`UnknownBillingLine`. Money strings parse via
    :func:`~app.money.types.dollars_str_to_cents`; a :class:`~app.money.types.MoneyParseError`
    propagates (the batch aborts). ``None`` fields are left untouched. An **empty string** clears
    the column to ``None`` — a bill legitimately loses a previously-recorded value (e.g. a paid
    amount reversed), and that is distinct from "no change" (``None`` in the edit).
    """
    line = db.get(BillingLine, edit.billing_line_id)
    if line is None or line.matter_id != matter.id:
        raise UnknownBillingLine(line_id=edit.billing_line_id)

    changed_money: list[str] = []
    for field_name, column in _MONEY_FIELDS:
        raw = getattr(edit, field_name)
        if raw is None:  # not provided — leave the column as-is
            continue
        if raw == "":  # explicit clear to None (a bill dropping a recorded value)
            if getattr(line, column) is not None:
                setattr(line, column, None)
                changed_money.append(field_name)
            continue
        # MoneyParseError propagates here — a bad string is never stored as a guess.
        setattr(line, column, dollars_str_to_cents(raw))
        changed_money.append(field_name)

    category_changed = False
    if edit.category is not None and line.category != edit.category.value:
        line.category = edit.category.value
        category_changed = True

    return changed_money, category_changed


def apply_billing_edits(
    db: Session, *, matter: Matter, pack: RulePack, batch: BillingLineEditBatch
) -> BillingEditOutcome:
    """Apply a batch of source-row billing-line edits atomically; return counts + fresh ledger.

    Per edit: the line must belong to the matter (:class:`UnknownBillingLine`); category (if
    present) is already an enum (the closed-taxonomy write rule holds at the schema); money strings
    (if present) parse via :func:`~app.money.types.dollars_str_to_cents` (:class:`MoneyParseError`
    propagates, aborting the batch). One ``billing_line_edited`` audit row per edited line records
    the changed field names. A single ``db.commit()`` at the end makes the batch atomic — a parse
    error or unknown line anywhere rolls the whole thing back (the caller's session is not
    committed until here). The ledger is recomputed AFTER the commit via
    :func:`~app.money.assemble.compute_matter_ledger` and returned — the FE never sums.
    """
    edited = 0
    recategorized = 0
    reparsed = 0

    try:
        for edit in batch.edits:
            changed_money, category_changed = _apply_one_edit(db, matter=matter, edit=edit)
            changed_fields = list(changed_money)
            if category_changed:
                changed_fields.append("category")
                recategorized += 1
            reparsed += len(changed_money)
            if changed_fields:
                edited += 1
                record_event(
                    db,
                    firm_id=matter.firm_id,
                    actor_id=None,
                    event_kind="billing_line_edited",
                    payload={
                        "line_id": str(edit.billing_line_id),
                        "changed_fields": changed_fields,
                    },
                )
    except Exception:
        # Atomicity: any typed refusal (unknown line, money parse) rolls back the whole batch,
        # so an earlier edit in the batch never survives a later failure.
        db.rollback()
        raise

    db.commit()
    ledger = compute_matter_ledger(db, matter=matter, pack=pack)
    return BillingEditOutcome(
        edited=edited,
        recategorized=recategorized,
        reparsed_money_fields=reparsed,
        ledger=ledger,
    )


__all__ = [
    "BillingEditOutcome",
    "UnknownBillingLine",
    "apply_billing_edits",
]
