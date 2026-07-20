"""Specials ledger v2 — pure: hash, dedup exclusion, billed-vs-paid basis, AMT payloads.

Properties (hypothesis, modest ``max_examples`` per the money-test house style) plus a hand-
computed golden micro-fixture that reconciles to the penny.
"""

from __future__ import annotations

import random
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.enums import LedgerCategory
from app.money.specials import (
    LedgerLine,
    amounts_for_registry,
    build_specials_ledger,
    line_contribution_cents,
    line_set_hash,
)
from app.money.types import cents_to_display

_CATEGORIES = [c.value for c in LedgerCategory]

# A small pool of document ids so the exclusion property actually excludes something.
_DOC_IDS = [uuid.UUID(int=i) for i in range(1, 6)]


def _line(
    *,
    doc: uuid.UUID,
    billed: int,
    adjusted: int | None,
    paid: int | None,
    outstanding: int | None,
    category: str,
    line_id: uuid.UUID | None = None,
) -> LedgerLine:
    return LedgerLine(
        id=line_id or uuid.uuid4(),
        document_id=doc,
        billed_cents=billed,
        adjusted_cents=adjusted,
        paid_cents=paid,
        outstanding_cents=outstanding,
        category=category,
    )


# --------------------------------------------------------------------------------------
# hypothesis strategies
# --------------------------------------------------------------------------------------

_amount = st.integers(min_value=0, max_value=10_000_000)
_opt_amount = st.none() | _amount
_line_st = st.builds(
    _line,
    doc=st.sampled_from(_DOC_IDS),
    billed=_amount,
    adjusted=_opt_amount,
    paid=_opt_amount,
    outstanding=_opt_amount,
    category=st.sampled_from(_CATEGORIES),
)
_lines_st = st.lists(_line_st, max_size=25)


# --------------------------------------------------------------------------------------
# line_set_hash
# --------------------------------------------------------------------------------------


@given(lines=_lines_st)
@settings(max_examples=60)
def test_hash_is_permutation_invariant(lines: list[LedgerLine]) -> None:
    shuffled = list(lines)
    random.Random(99).shuffle(shuffled)
    assert line_set_hash(lines) == line_set_hash(shuffled)


@given(lines=_lines_st)
@settings(max_examples=60)
def test_hash_identical_sets_identical(lines: list[LedgerLine]) -> None:
    assert line_set_hash(lines) == line_set_hash(list(lines))


def test_hash_changes_on_membership() -> None:
    a = _line(
        doc=_DOC_IDS[0], billed=100, adjusted=None, paid=None, outstanding=None, category="er"
    )
    base = [a]
    extra = _line(
        doc=_DOC_IDS[1], billed=200, adjusted=None, paid=None, outstanding=None, category="er"
    )
    assert line_set_hash(base) != line_set_hash(base + [extra])


def test_hash_changes_on_amount() -> None:
    lid = uuid.uuid4()
    a = _line(
        doc=_DOC_IDS[0],
        billed=100,
        adjusted=None,
        paid=None,
        outstanding=None,
        category="er",
        line_id=lid,
    )
    b = _line(
        doc=_DOC_IDS[0],
        billed=101,
        adjusted=None,
        paid=None,
        outstanding=None,
        category="er",
        line_id=lid,
    )
    assert line_set_hash([a]) != line_set_hash([b])


def test_hash_changes_on_category() -> None:
    lid = uuid.uuid4()
    a = _line(
        doc=_DOC_IDS[0],
        billed=100,
        adjusted=None,
        paid=None,
        outstanding=None,
        category="er",
        line_id=lid,
    )
    b = _line(
        doc=_DOC_IDS[0],
        billed=100,
        adjusted=None,
        paid=None,
        outstanding=None,
        category="imaging",
        line_id=lid,
    )
    assert line_set_hash([a]) != line_set_hash([b])


def test_hash_distinguishes_none_from_zero() -> None:
    """A None optional column hashes differently from an explicit 0 (distinct reconciliations)."""
    lid = uuid.uuid4()
    none_paid = _line(
        doc=_DOC_IDS[0],
        billed=100,
        adjusted=None,
        paid=None,
        outstanding=None,
        category="er",
        line_id=lid,
    )
    zero_paid = _line(
        doc=_DOC_IDS[0],
        billed=100,
        adjusted=None,
        paid=0,
        outstanding=None,
        category="er",
        line_id=lid,
    )
    assert line_set_hash([none_paid]) != line_set_hash([zero_paid])


# --------------------------------------------------------------------------------------
# exclusion
# --------------------------------------------------------------------------------------


@given(lines=_lines_st, extra=_line_st)
@settings(max_examples=80)
def test_excluded_line_changes_no_total_and_no_hash(
    lines: list[LedgerLine], extra: LedgerLine
) -> None:
    """Adding any line whose document_id is excluded moves no total and no hash; id is listed."""
    excluded = frozenset({extra.document_id})
    base = build_specials_ledger(lines, excluded_doc_ids=excluded, basis="billed")
    with_extra = build_specials_ledger(lines + [extra], excluded_doc_ids=excluded, basis="billed")

    assert with_extra.grand_total == base.grand_total
    assert with_extra.by_category == base.by_category
    assert with_extra.line_set_hash == base.line_set_hash
    assert with_extra.demand_basis_total_cents == base.demand_basis_total_cents
    assert str(extra.id) in with_extra.excluded_line_ids
    assert str(extra.id) not in with_extra.included_line_ids


def test_excluded_ids_are_sorted_and_disjoint_from_included() -> None:
    keep = _line(
        doc=_DOC_IDS[0], billed=100, adjusted=None, paid=None, outstanding=None, category="er"
    )
    drop1 = _line(
        doc=_DOC_IDS[1], billed=200, adjusted=None, paid=None, outstanding=None, category="er"
    )
    drop2 = _line(
        doc=_DOC_IDS[1], billed=300, adjusted=None, paid=None, outstanding=None, category="er"
    )
    led = build_specials_ledger(
        [keep, drop1, drop2], excluded_doc_ids=frozenset({_DOC_IDS[1]}), basis="billed"
    )
    assert led.included_line_ids == (str(keep.id),)
    assert led.excluded_line_ids == tuple(sorted((str(drop1.id), str(drop2.id))))
    assert set(led.included_line_ids).isdisjoint(led.excluded_line_ids)


# --------------------------------------------------------------------------------------
# basis
# --------------------------------------------------------------------------------------


@given(lines=_lines_st)
@settings(max_examples=80)
def test_billed_basis_demand_equals_grand_billed(lines: list[LedgerLine]) -> None:
    led = build_specials_ledger(lines, excluded_doc_ids=frozenset(), basis="billed")
    assert led.demand_basis_total_cents == led.grand_total.billed_cents
    assert led.missing_paid_line_ids == ()


@given(lines=_lines_st)
@settings(max_examples=80)
def test_paid_basis_demand_sums_present_paid_and_lists_gaps(lines: list[LedgerLine]) -> None:
    led = build_specials_ledger(lines, excluded_doc_ids=frozenset(), basis="paid")
    expected_total = sum(line.paid_cents for line in lines if line.paid_cents is not None)
    expected_gaps = tuple(sorted(str(line.id) for line in lines if line.paid_cents is None))
    assert led.demand_basis_total_cents == expected_total
    assert led.missing_paid_line_ids == expected_gaps


def test_invalid_basis_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_specials_ledger([], excluded_doc_ids=frozenset(), basis="accrued")


# --------------------------------------------------------------------------------------
# category sums
# --------------------------------------------------------------------------------------


@given(lines=_lines_st, excluded=st.sets(st.sampled_from(_DOC_IDS), max_size=3))
@settings(max_examples=80)
def test_category_subtotals_sum_to_grand_total(
    lines: list[LedgerLine], excluded: set[uuid.UUID]
) -> None:
    led = build_specials_ledger(lines, excluded_doc_ids=frozenset(excluded), basis="billed")
    for column in ("billed_cents", "adjusted_cents", "paid_cents", "outstanding_cents"):
        category_sum = sum(getattr(cols, column) for cols in led.by_category.values())
        assert category_sum == getattr(led.grand_total, column)


@given(lines=_lines_st)
@settings(max_examples=60)
def test_category_line_ids_partition_included(lines: list[LedgerLine]) -> None:
    led = build_specials_ledger(lines, excluded_doc_ids=frozenset(), basis="billed")
    from_map = sorted(lid for ids in led.category_line_ids.values() for lid in ids)
    assert from_map == sorted(led.included_line_ids)


# --------------------------------------------------------------------------------------
# amounts_for_registry
# --------------------------------------------------------------------------------------


def _golden_lines() -> tuple[list[LedgerLine], uuid.UUID]:
    """Six lines, mixed categories, one excluded dup doc, adjusted/paid gaps — hand-computed.

    Returns the lines plus the excluded duplicate's document id.
    """
    d_er = uuid.UUID(int=101)
    d_img = uuid.UUID(int=102)
    d_pt = uuid.UUID(int=103)
    d_dup = uuid.UUID(int=104)
    return [
        # id ordering fixed so included_line_ids is predictable
        _line(
            doc=d_er,
            billed=10_000,
            adjusted=2_000,
            paid=6_000,
            outstanding=2_000,
            category="er",
            line_id=uuid.UUID(int=1),
        ),
        _line(
            doc=d_er,
            billed=5_000,
            adjusted=None,
            paid=5_000,
            outstanding=0,
            category="er",
            line_id=uuid.UUID(int=2),
        ),
        _line(
            doc=d_img,
            billed=30_000,
            adjusted=10_000,
            paid=15_000,
            outstanding=5_000,
            category="imaging",
            line_id=uuid.UUID(int=3),
        ),
        _line(
            doc=d_img,
            billed=4_000,
            adjusted=1_000,
            paid=3_000,
            outstanding=0,
            category="imaging",
            line_id=uuid.UUID(int=4),
        ),
        _line(
            doc=d_pt,
            billed=8_000,
            adjusted=None,
            paid=None,
            outstanding=8_000,
            category="pt_chiro",
            line_id=uuid.UUID(int=5),
        ),
        # excluded duplicate document — must contribute to nothing
        _line(
            doc=d_dup,
            billed=99_999,
            adjusted=99_999,
            paid=99_999,
            outstanding=99_999,
            category="surgery",
            line_id=uuid.UUID(int=6),
        ),
    ], d_dup


def test_golden_micro_fixture_reconciles_to_the_penny() -> None:
    lines, d_dup = _golden_lines()
    led = build_specials_ledger(lines, excluded_doc_ids=frozenset({d_dup}), basis="billed")

    # ER: billed 15000, adjusted 2000, paid 11000, outstanding 2000
    er = led.by_category["er"]
    assert (er.billed_cents, er.adjusted_cents, er.paid_cents, er.outstanding_cents) == (
        15_000,
        2_000,
        11_000,
        2_000,
    )
    # IMAGING: billed 34000, adjusted 11000, paid 18000, outstanding 5000
    img = led.by_category["imaging"]
    assert (img.billed_cents, img.adjusted_cents, img.paid_cents, img.outstanding_cents) == (
        34_000,
        11_000,
        18_000,
        5_000,
    )
    # PT_CHIRO: billed 8000, adjusted 0 (None), paid 0 (None), outstanding 8000
    pt = led.by_category["pt_chiro"]
    assert (pt.billed_cents, pt.adjusted_cents, pt.paid_cents, pt.outstanding_cents) == (
        8_000,
        0,
        0,
        8_000,
    )
    # SURGERY excluded entirely
    assert "surgery" not in led.by_category
    # GRAND: billed 57000, adjusted 13000, paid 29000, outstanding 15000
    assert (
        led.grand_total.billed_cents,
        led.grand_total.adjusted_cents,
        led.grand_total.paid_cents,
        led.grand_total.outstanding_cents,
    ) == (57_000, 13_000, 29_000, 15_000)
    assert led.demand_basis_total_cents == 57_000
    assert led.excluded_line_ids == (str(uuid.UUID(int=6)),)


def test_amounts_key_vocabulary_and_refs_exact() -> None:
    lines, d_dup = _golden_lines()
    led = build_specials_ledger(lines, excluded_doc_ids=frozenset({d_dup}), basis="billed")
    facts = amounts_for_registry(led)
    by_key = {f.key: f for f in facts}

    assert set(by_key) == {
        "specials.grand.billed",
        "specials.grand.paid",
        "specials.grand.outstanding",
        "specials.category.er.billed",
        "specials.category.imaging.billed",
        "specials.category.pt_chiro.billed",
        "specials.demand_basis",
    }

    # cents exact + display via cents_to_display
    assert by_key["specials.grand.billed"].value_cents == 57_000
    assert by_key["specials.grand.billed"].display_form == cents_to_display(57_000)
    assert by_key["specials.demand_basis"].value_cents == 57_000
    assert by_key["specials.category.imaging.billed"].value_cents == 34_000

    # every payload pinned to the ledger hash
    assert all(f.ledger_hash == led.line_set_hash for f in facts)

    # category ref carries exactly that category's included line ids
    img_ref = by_key["specials.category.imaging.billed"].ledger_ref
    assert img_ref["category"] == "imaging"
    assert img_ref["column"] == "billed"
    assert tuple(img_ref["line_ids"]) == led.category_line_ids["imaging"]
    assert tuple(img_ref["line_ids"]) == (str(uuid.UUID(int=3)), str(uuid.UUID(int=4)))

    # grand/demand refs span all included ids, category None
    grand_ref = by_key["specials.grand.billed"].ledger_ref
    assert grand_ref["category"] is None
    assert tuple(grand_ref["line_ids"]) == led.included_line_ids
    assert by_key["specials.demand_basis"].ledger_ref["column"] == "demand_basis"


def test_amounts_always_emits_billed_and_demand_even_when_zero() -> None:
    """grand.billed + demand_basis are unconditional; paid/outstanding suppressed when zero-sum."""
    led = build_specials_ledger([], excluded_doc_ids=frozenset(), basis="billed")
    keys = {f.key for f in amounts_for_registry(led)}
    assert keys == {"specials.grand.billed", "specials.demand_basis"}


def test_amounts_paid_column_suppressed_when_all_none() -> None:
    line = _line(
        doc=_DOC_IDS[0], billed=1_000, adjusted=None, paid=None, outstanding=None, category="er"
    )
    led = build_specials_ledger([line], excluded_doc_ids=frozenset(), basis="billed")
    keys = {f.key for f in amounts_for_registry(led)}
    assert "specials.grand.paid" not in keys
    assert "specials.grand.outstanding" not in keys
    assert "specials.category.er.billed" in keys


# --------------------------------------------------------------------------------------
# line_contribution_cents — the per-line share of an AMT column (provenance display)
# --------------------------------------------------------------------------------------


def test_line_contribution_direct_columns_read_their_field() -> None:
    line = _line(
        doc=_DOC_IDS[0], billed=9_200, adjusted=None, paid=5_000, outstanding=4_200, category="er"
    )
    assert line_contribution_cents(line, column="billed", basis=None) == 9_200
    assert line_contribution_cents(line, column="paid", basis=None) == 5_000
    assert line_contribution_cents(line, column="outstanding", basis=None) == 4_200


def test_line_contribution_missing_paid_is_none_never_zero() -> None:
    """A line with no paid figure contributes None under the paid column — no silent zero-fill."""
    line = _line(
        doc=_DOC_IDS[0], billed=1_000, adjusted=None, paid=None, outstanding=None, category="er"
    )
    assert line_contribution_cents(line, column="paid", basis=None) is None
    assert line_contribution_cents(line, column="outstanding", basis=None) is None


def test_line_contribution_demand_basis_resolves_via_basis() -> None:
    line = _line(
        doc=_DOC_IDS[0], billed=9_200, adjusted=None, paid=5_000, outstanding=None, category="er"
    )
    assert line_contribution_cents(line, column="demand_basis", basis="billed") == 9_200
    assert line_contribution_cents(line, column="demand_basis", basis="paid") == 5_000
    # An unresolvable basis (e.g. the caller's pack pin refused) yields None, never a guess.
    assert line_contribution_cents(line, column="demand_basis", basis=None) is None


def test_line_contribution_unknown_column_refuses() -> None:
    line = _line(
        doc=_DOC_IDS[0], billed=1_000, adjusted=500, paid=None, outstanding=None, category="er"
    )
    with pytest.raises(ValueError, match="unknown ledger column"):
        line_contribution_cents(line, column="adjusted", basis=None)
    with pytest.raises(ValueError, match="unknown ledger column"):
        line_contribution_cents(line, column="demand_basis", basis="weird")
