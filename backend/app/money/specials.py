"""The specials ledger v2 — a pure, order-independent view with dedup exclusion + AMT payloads.

This layer sits above the primitive :func:`app.money.ledger.rollup` and adds the four things
Wave B2 owns: a content hash over the exact line set (``line_set_hash``), document-level dedup
exclusion applied *before any sum* (money_engine §4 — double-counting is structurally impossible,
not filtered after the fact), the jurisdiction billed-vs-paid demand basis, and the deterministic
``[[AMT]]`` emission payloads the tokenizer mints from.

Discipline (AGENTS money boundary + inv 10):

* Integer cents in, integer cents out — floats are banned; nothing here constructs one.
* Every function is **pure**: no I/O, no ``datetime.now()``. DB access lives only in
  :mod:`app.money.assemble`. The ledger is a *derived view*, always recomputable from the
  billing-line set — nothing here writes a total anywhere.
* ``None`` in an optional money column means "no data", encoded *distinctly from* ``0`` in the
  hash and never silently zero-filled in a paid-basis demand (substituting billed on a paid-basis
  jurisdiction is a legal call code must not make — the gap is surfaced instead).
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from app.models.schemas import AmountFact
from app.money.ledger import BillingLineLike, LedgerColumns, rollup
from app.money.types import Cents, cents_to_display

_VALID_BASES = ("billed", "paid")


class LedgerLineLike(Protocol):
    """What the specials ledger needs from a line: identity + source doc + money + category.

    Satisfied by :class:`LedgerLine`, the thin dataclass :mod:`app.money.assemble` builds from
    ORM rows. (The ORM ``BillingLine`` row itself lacks a ``document_id`` attribute — that id
    lives inside its ``anchor`` JSON, so assemble parses it out into this shape.)

    Members are read-only ``@property`` declarations so a *frozen* dataclass (:class:`LedgerLine`)
    satisfies them — a plain annotation would demand a settable attribute and reject the frozen
    carrier the money layer deliberately uses.
    """

    @property
    def id(self) -> uuid.UUID: ...
    @property
    def document_id(self) -> uuid.UUID: ...
    @property
    def billed_cents(self) -> int: ...
    @property
    def adjusted_cents(self) -> int | None: ...
    @property
    def paid_cents(self) -> int | None: ...
    @property
    def outstanding_cents(self) -> int | None: ...
    @property
    def category(self) -> str: ...


@dataclass(frozen=True)
class LedgerLine:
    """The concrete carrier :mod:`app.money.assemble` emits.

    Satisfies both :class:`LedgerLineLike` (identity + source doc) and
    :class:`app.money.ledger.BillingLineLike` (the money columns + category), so the same object
    feeds the exclusion/hash logic here and the primitive :func:`~app.money.ledger.rollup`.
    """

    id: uuid.UUID
    document_id: uuid.UUID
    billed_cents: int
    adjusted_cents: int | None
    paid_cents: int | None
    outstanding_cents: int | None
    category: str


@dataclass(frozen=True)
class SpecialsLedger:
    """A computed specials ledger: per-category columns, grand total, and the audit spine.

    ``line_set_hash`` is a content hash over the *included* lines (see :func:`line_set_hash`) —
    it is what an ``[[AMT]]`` snapshots, so any downstream drift is detectable. ``basis`` is the
    jurisdiction billed-vs-paid basis (rules-owned); ``demand_basis_total_cents`` is the figure
    the demand leads with under that basis. ``excluded_line_ids`` and ``missing_paid_line_ids``
    are surfaced for visibility, never swallowed.
    """

    by_category: dict[str, LedgerColumns]
    grand_total: LedgerColumns
    line_set_hash: str
    included_line_ids: tuple[str, ...]
    excluded_line_ids: tuple[str, ...]
    basis: str
    demand_basis_total_cents: Cents
    missing_paid_line_ids: tuple[str, ...]
    # category -> its included line ids, so an [[AMT]] category ref is exact (the by_category
    # columns are sums and don't retain membership). Populated by build_specials_ledger.
    category_line_ids: dict[str, tuple[str, ...]]


def _line_key(line: LedgerLineLike) -> tuple[str, str, str, str, str, str]:
    """A per-line tuple of *strings* for hashing.

    ``str()`` on each field keeps ``None`` (``"None"``) distinct from ``0`` (``"0"``) — the two
    must hash differently, since "no data" and "zero dollars" are different reconciliations.
    """
    return (
        str(line.id),
        str(line.billed_cents),
        str(line.adjusted_cents),
        str(line.paid_cents),
        str(line.outstanding_cents),
        str(line.category),
    )


def line_set_hash(lines: Sequence[LedgerLineLike]) -> str:
    """A SHA-256 hex digest over the *sorted* per-line tuples — order-independent.

    Any change to membership, a money amount, a category, or a ``None``-vs-``0`` distinction
    changes the digest; shuffling the input does not. This is the ledger's cache key and the
    value an ``[[AMT]]`` pins itself to (inv 10).
    """
    hasher = hashlib.sha256()
    for key in sorted(_line_key(line) for line in lines):
        # A record separator between fields and a line separator between records so no
        # concatenation collision is possible ("1","23" vs "12","3").
        hasher.update(("\x1f".join(key) + "\x1e").encode("utf-8"))
    return hasher.hexdigest()


def build_specials_ledger(
    lines: Sequence[LedgerLineLike],
    *,
    excluded_doc_ids: frozenset[uuid.UUID],
    basis: str,
) -> SpecialsLedger:
    """Build the specials ledger: exclude dup docs, roll up, and compute the demand basis.

    Steps (order matters — exclusion precedes every sum so double-counting is structurally
    impossible, money_engine §4):

    1. ``basis`` must be ``"billed"`` or ``"paid"``, else :class:`ValueError` (a typed refusal —
       the value is rules-owned and an unknown basis is a bug, not a fallback).
    2. Drop every line whose ``document_id`` is in ``excluded_doc_ids`` *before* rolling up.
    3. :func:`~app.money.ledger.rollup` the surviving lines.
    4. Demand basis: ``"billed"`` → grand billed; ``"paid"`` → sum of ``paid_cents`` over
       included lines where paid is present. Lines with ``paid is None`` under paid basis are
       listed in ``missing_paid_line_ids`` — the gap is surfaced, never silently zero-filled or
       billed-substituted (a legal call code must not make).
    5. The hash and the category membership map cover the *included* lines only.
    """
    if basis not in _VALID_BASES:
        raise ValueError(f"basis must be one of {_VALID_BASES!r}, got {basis!r}")

    included = [line for line in lines if line.document_id not in excluded_doc_ids]
    excluded = [line for line in lines if line.document_id in excluded_doc_ids]

    # rollup only *reads* the money columns + category, all of which LedgerLineLike carries;
    # the cast bridges the two structural protocols (LedgerLineLike is read-only so a frozen
    # LedgerLine satisfies it, ledger.BillingLineLike declares the same fields settable). Safe:
    # rollup never mutates a line.
    summary = rollup(cast("Sequence[BillingLineLike]", included))

    included_ids = tuple(sorted(str(line.id) for line in included))
    excluded_ids = tuple(sorted(str(line.id) for line in excluded))

    category_line_ids: dict[str, tuple[str, ...]] = {}
    per_category: dict[str, list[str]] = defaultdict(list)
    for line in included:
        per_category[line.category].append(str(line.id))
    for category, ids in per_category.items():
        category_line_ids[category] = tuple(sorted(ids))

    if basis == "billed":
        demand_total: Cents = summary.grand_total.billed_cents
        missing_paid: tuple[str, ...] = ()
    else:  # paid basis
        demand_total = sum(line.paid_cents for line in included if line.paid_cents is not None)
        missing_paid = tuple(sorted(str(line.id) for line in included if line.paid_cents is None))

    return SpecialsLedger(
        by_category=summary.by_category,
        grand_total=summary.grand_total,
        line_set_hash=line_set_hash(included),
        included_line_ids=included_ids,
        excluded_line_ids=excluded_ids,
        basis=basis,
        demand_basis_total_cents=demand_total,
        missing_paid_line_ids=missing_paid,
        category_line_ids=category_line_ids,
    )


def line_contribution_cents(
    line: BillingLineLike, *, column: str, basis: str | None
) -> Cents | None:
    """One line's share of an ``[[AMT]]`` ledger column — provenance display, never a new sum.

    Direct columns read their own field; ``paid``/``outstanding`` may be ``None`` ("no data"),
    surfaced as ``None`` and never zero-filled (the same discipline as the rollup).
    ``demand_basis`` resolves through the jurisdiction ``basis`` (rules-owned) — a ``None`` basis
    (e.g. the caller's pack pin refused) returns ``None`` rather than guessing a column. Any other
    column is a :class:`ValueError` (typed refusal — the vocabulary is fixed by
    :func:`amounts_for_registry`).
    """
    resolved = basis if column == "demand_basis" else column
    if resolved is None:
        return None
    if resolved == "billed":
        return line.billed_cents
    if resolved == "paid":
        return line.paid_cents
    if resolved == "outstanding":
        return line.outstanding_cents
    raise ValueError(f"unknown ledger column {column!r} (basis {basis!r})")


def _amount_fact(
    *,
    key: str,
    value_cents: Cents,
    line_ids: tuple[str, ...],
    category: str | None,
    column: str,
    ledger_hash: str,
) -> AmountFact:
    """Build one AMT payload with a display form and a ledger ref pinned to the hash."""
    return AmountFact(
        key=key,
        value_cents=value_cents,
        display_form=cents_to_display(value_cents),
        ledger_ref={"line_ids": list(line_ids), "category": category, "column": column},
        ledger_hash=ledger_hash,
    )


def amounts_for_registry(ledger: SpecialsLedger) -> list[AmountFact]:
    """The deterministic ``[[AMT]]`` emission payloads for a computed ledger.

    Key vocabulary (evals + tokenizer are specced against these exact strings):

    * ``"specials.grand.billed"`` — grand billed (always emitted)
    * ``"specials.grand.paid"`` — grand paid (emitted when the grand paid sum is non-zero)
    * ``"specials.grand.outstanding"`` — grand outstanding (emitted when its grand sum is non-zero)
    * ``f"specials.category.{category}.billed"`` — one per present category
    * ``"specials.demand_basis"`` — the demand-basis total (always emitted)

    Emission rule (kept simple + documented): ``grand.billed`` and ``demand_basis`` are always
    emitted; ``grand.paid`` / ``grand.outstanding`` are emitted only when that column's grand sum
    is non-zero (a column that never got a figure sums to ``0`` and is not asserted as ``$0.00`` —
    the rollup can't distinguish all-``None`` from all-present-zero, so the conservative rule
    keys off a non-zero sum); every present category emits its billed subtotal. Every payload's
    ``ledger_hash`` is ``ledger.line_set_hash``; each category ref carries exactly that category's
    included line ids; ``display_form`` is via :func:`~app.money.types.cents_to_display`.
    """
    all_ids = ledger.included_line_ids
    grand = ledger.grand_total
    facts: list[AmountFact] = []

    facts.append(
        _amount_fact(
            key="specials.grand.billed",
            value_cents=grand.billed_cents,
            line_ids=all_ids,
            category=None,
            column="billed",
            ledger_hash=ledger.line_set_hash,
        )
    )

    if grand.paid_cents != 0:
        facts.append(
            _amount_fact(
                key="specials.grand.paid",
                value_cents=grand.paid_cents,
                line_ids=all_ids,
                category=None,
                column="paid",
                ledger_hash=ledger.line_set_hash,
            )
        )

    if grand.outstanding_cents != 0:
        facts.append(
            _amount_fact(
                key="specials.grand.outstanding",
                value_cents=grand.outstanding_cents,
                line_ids=all_ids,
                category=None,
                column="outstanding",
                ledger_hash=ledger.line_set_hash,
            )
        )

    for category in sorted(ledger.by_category):
        cols = ledger.by_category[category]
        facts.append(
            _amount_fact(
                key=f"specials.category.{category}.billed",
                value_cents=cols.billed_cents,
                line_ids=ledger.category_line_ids.get(category, ()),
                category=category,
                column="billed",
                ledger_hash=ledger.line_set_hash,
            )
        )

    facts.append(
        _amount_fact(
            key="specials.demand_basis",
            value_cents=ledger.demand_basis_total_cents,
            line_ids=all_ids,
            category=None,
            column="demand_basis",
            ledger_hash=ledger.line_set_hash,
        )
    )

    return facts
