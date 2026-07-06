"""Billing-line edit service tests (M4 Wave B2) — source-row edits, atomicity, derived recompute.

Local in-file engine/session fixtures (modeled on ``tests/money/test_assemble.py``, kept here so
this suite does not depend on a shared conftest). Synthetic data only — no PHI.

Coverage: a category recategorization moves a line between ledger buckets and changes the hash; a
money-string edit lands exact cents; an empty string clears a column to ``None``; a malformed
string raises :class:`MoneyParseError` and commits NOTHING (the batch is atomic — an earlier edit
in the same batch is rolled back too); an unknown line is typed; and one ``billing_line_edited``
audit row is written per edited line.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import seed_dev_firm_and_user
from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.tenancy import tenant_add
from app.models.enums import DocStatus, DocType, GateState, LedgerCategory
from app.models.orm import AuditEvent, BillingLine, CaseDocument, Matter, User
from app.models.schemas import BillingLineEdit, BillingLineEditBatch
from app.money.edits import UnknownBillingLine, apply_billing_edits
from app.money.types import MoneyParseError
from app.rules.loader import load_pack

_INCIDENT = dt.date(2026, 1, 15)
_DOS = dt.date(2026, 2, 1)


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=2500,
        )
    )
    create_all_for_tests(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def dev_user(db: Session) -> User:
    return seed_dev_firm_and_user(db)


@pytest.fixture
def matter(db: Session, dev_user: User) -> Matter:
    m = Matter(
        client_display_name="Test Client",
        claim_type="mva",
        incident_date=_INCIDENT,
        jurisdiction="AZ",
        gate_state=GateState.EVIDENCE_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    tenant_add(db, m, dev_user.firm_id)
    db.commit()
    return m


def _add_document(db: Session, matter: Matter) -> CaseDocument:
    doc = CaseDocument(
        matter_id=matter.id,
        doc_type=DocType.BILL.value,
        source_label="bill.pdf",
        filename="bill.pdf",
        page_count=1,
        dedup_status="unique",
        status=DocStatus.EXTRACTED.value,
    )
    tenant_add(db, doc, matter.firm_id)
    db.commit()
    return doc


def _add_line(
    db: Session,
    matter: Matter,
    doc: CaseDocument,
    *,
    billed: int,
    category: LedgerCategory,
    paid: int | None = None,
) -> BillingLine:
    line = BillingLine(
        matter_id=matter.id,
        provider="Provider",
        date_of_service=_DOS,
        billed_cents=billed,
        paid_cents=paid,
        category=category.value,
        anchor={"document_id": str(doc.id), "page": 1},
    )
    tenant_add(db, line, matter.firm_id)
    db.commit()
    return line


def _pack():
    return load_pack("AZ")


# --------------------------------------------------------------------------------------
# Recategorization -> ledger recomputes, hash changes
# --------------------------------------------------------------------------------------


def test_recategorize_moves_bucket_and_changes_hash(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER)

    before = _pack()
    from app.money.assemble import compute_matter_ledger

    ledger_before = compute_matter_ledger(db, matter=matter, pack=before)
    assert "er" in ledger_before.by_category
    assert "imaging" not in ledger_before.by_category

    batch = BillingLineEditBatch(
        edits=[BillingLineEdit(billing_line_id=line.id, category=LedgerCategory.IMAGING)]
    )
    outcome = apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)

    assert outcome.edited == 1
    assert outcome.recategorized == 1
    assert outcome.reparsed_money_fields == 0
    assert "imaging" in outcome.ledger.by_category
    assert "er" not in outcome.ledger.by_category
    assert outcome.ledger.by_category["imaging"].billed_cents == 10_000
    assert outcome.ledger.line_set_hash != ledger_before.line_set_hash


# --------------------------------------------------------------------------------------
# Money-string edits — exact cents, empty-string clear
# --------------------------------------------------------------------------------------


def test_money_string_edit_lands_exact_cents(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER)

    batch = BillingLineEditBatch(
        edits=[BillingLineEdit(billing_line_id=line.id, billed="$1,234.56", paid="$1,000.00")]
    )
    outcome = apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)

    db.refresh(line)
    assert line.billed_cents == 123_456
    assert line.paid_cents == 100_000
    assert outcome.reparsed_money_fields == 2
    assert outcome.recategorized == 0
    assert outcome.ledger.grand_total.billed_cents == 123_456


def test_empty_string_clears_column_to_none(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER, paid=6_000)
    assert line.paid_cents == 6_000

    batch = BillingLineEditBatch(edits=[BillingLineEdit(billing_line_id=line.id, paid="")])
    outcome = apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)

    db.refresh(line)
    assert line.paid_cents is None
    assert outcome.edited == 1
    assert outcome.reparsed_money_fields == 1


def test_none_field_leaves_column_untouched(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER, paid=6_000)

    # An edit that provides only billed leaves paid as-is (None means "not provided").
    batch = BillingLineEditBatch(edits=[BillingLineEdit(billing_line_id=line.id, billed="$200.00")])
    apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)

    db.refresh(line)
    assert line.billed_cents == 20_000
    assert line.paid_cents == 6_000  # untouched


# --------------------------------------------------------------------------------------
# Atomicity — bad string / unknown line rolls the whole batch back
# --------------------------------------------------------------------------------------


def test_bad_money_string_propagates_and_commits_nothing(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line_a = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER)
    line_b = _add_line(db, matter, doc, billed=20_000, category=LedgerCategory.IMAGING)

    # First edit is valid; second is malformed. The batch must abort with NOTHING committed —
    # including the first edit (atomicity).
    batch = BillingLineEditBatch(
        edits=[
            BillingLineEdit(billing_line_id=line_a.id, billed="$500.00"),
            BillingLineEdit(billing_line_id=line_b.id, billed="1.234"),  # 3-decimal -> parse error
        ]
    )
    with pytest.raises(MoneyParseError):
        apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)

    db.refresh(line_a)
    db.refresh(line_b)
    assert line_a.billed_cents == 10_000  # first edit rolled back too
    assert line_b.billed_cents == 20_000
    # No audit rows survived the rollback.
    audits = list(
        db.scalars(select(AuditEvent).where(AuditEvent.event_kind == "billing_line_edited"))
    )
    assert audits == []


def test_negative_money_string_is_parse_error(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER)
    batch = BillingLineEditBatch(edits=[BillingLineEdit(billing_line_id=line.id, billed="-$5.00")])
    with pytest.raises(MoneyParseError):
        apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)
    db.refresh(line)
    assert line.billed_cents == 10_000


def test_unknown_line_is_typed(db: Session, matter: Matter) -> None:
    import uuid

    bogus = uuid.uuid4()
    batch = BillingLineEditBatch(edits=[BillingLineEdit(billing_line_id=bogus, billed="$1.00")])
    with pytest.raises(UnknownBillingLine) as excinfo:
        apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)
    assert excinfo.value.line_id == bogus


def test_cross_matter_line_is_unknown(db: Session, matter: Matter, dev_user: User) -> None:
    # A line on another matter (same firm) is not on THIS matter -> UnknownBillingLine.
    other = Matter(
        client_display_name="Other",
        claim_type="mva",
        incident_date=_INCIDENT,
        jurisdiction="AZ",
        gate_state=GateState.EVIDENCE_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    tenant_add(db, other, dev_user.firm_id)
    db.commit()
    other_doc = _add_document(db, other)
    other_line = _add_line(db, other, other_doc, billed=5_000, category=LedgerCategory.ER)

    batch = BillingLineEditBatch(
        edits=[BillingLineEdit(billing_line_id=other_line.id, billed="$1.00")]
    )
    with pytest.raises(UnknownBillingLine):
        apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)


# --------------------------------------------------------------------------------------
# Audit — one row per edited line, with changed_fields
# --------------------------------------------------------------------------------------


def test_audit_row_per_edited_line(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line_a = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER)
    line_b = _add_line(db, matter, doc, billed=20_000, category=LedgerCategory.IMAGING)

    batch = BillingLineEditBatch(
        edits=[
            BillingLineEdit(
                billing_line_id=line_a.id, billed="$100.00", category=LedgerCategory.SURGERY
            ),
            BillingLineEdit(billing_line_id=line_b.id, paid="$50.00"),
        ]
    )
    outcome = apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)
    assert outcome.edited == 2

    audits = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_kind == "billing_line_edited")
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    assert len(audits) == 2
    by_line = {a.payload["line_id"]: a.payload["changed_fields"] for a in audits}
    assert set(by_line[str(line_a.id)]) == {"billed", "category"}
    assert by_line[str(line_b.id)] == ["paid"]


def test_noop_edit_writes_no_audit(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line = _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER)
    # Re-assert the SAME category -> no change -> no audit, not counted as edited.
    batch = BillingLineEditBatch(
        edits=[BillingLineEdit(billing_line_id=line.id, category=LedgerCategory.ER)]
    )
    outcome = apply_billing_edits(db, matter=matter, pack=_pack(), batch=batch)
    assert outcome.edited == 0
    assert outcome.recategorized == 0
    audits = list(
        db.scalars(select(AuditEvent).where(AuditEvent.event_kind == "billing_line_edited"))
    )
    assert audits == []
