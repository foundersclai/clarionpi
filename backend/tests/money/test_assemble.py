"""The thin DB layer: BillingLine -> LedgerLine, dedup exclusion, and the composed ledger.

Local in-file engine/session fixtures (modeled on ``tests/corpus/conftest.py`` but kept here so
this suite does not depend on or edit a shared conftest). Synthetic data only — no PHI.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import seed_dev_firm_and_user
from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.tenancy import tenant_add
from app.models.enums import (
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    LedgerCategory,
)
from app.models.orm import BillingLine, CaseDocument, DedupDecision, Matter, User
from app.money.assemble import (
    MalformedAnchor,
    collect_ledger_inputs,
    compute_matter_ledger,
)
from app.rules.loader import load_pack

_INCIDENT = dt.date(2026, 1, 15)
_DOS = dt.date(2026, 2, 1)


# --------------------------------------------------------------------------------------
# Local fixtures (in-memory engine, open session, seeded dev tenant + matter)
# --------------------------------------------------------------------------------------


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
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    tenant_add(db, m, dev_user.firm_id)
    db.commit()
    return m


# --------------------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------------------


def _add_document(
    db: Session, matter: Matter, *, dedup_status: DedupStatus = DedupStatus.UNIQUE
) -> CaseDocument:
    doc = CaseDocument(
        matter_id=matter.id,
        doc_type=DocType.BILL.value,
        source_label="bill.pdf",
        filename="bill.pdf",
        page_count=1,
        dedup_status=dedup_status.value,
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
    anchor: dict | None = None,
) -> BillingLine:
    line = BillingLine(
        matter_id=matter.id,
        provider="Provider",
        date_of_service=_DOS,
        billed_cents=billed,
        paid_cents=paid,
        category=category.value,
        anchor=anchor if anchor is not None else {"document_id": str(doc.id), "page": 1},
    )
    tenant_add(db, line, matter.firm_id)
    db.commit()
    return line


def _superseded_decision(db: Session, matter: Matter, doc: CaseDocument) -> DedupDecision:
    decision = DedupDecision(
        matter_id=matter.id,
        document_id=doc.id,
        status=DedupStatus.DUPLICATE_OF.value,
        resolution=DedupResolution.SUPERSEDED.value,
    )
    tenant_add(db, decision, matter.firm_id)
    db.commit()
    return decision


def _pending_decision(db: Session, matter: Matter, doc: CaseDocument) -> DedupDecision:
    decision = DedupDecision(
        matter_id=matter.id,
        document_id=doc.id,
        status=DedupStatus.DUPLICATE_OF.value,
        resolution=DedupResolution.PENDING.value,
    )
    tenant_add(db, decision, matter.firm_id)
    db.commit()
    return decision


# --------------------------------------------------------------------------------------
# collect_ledger_inputs — shaping + exclusion rules
# --------------------------------------------------------------------------------------


def test_collect_shapes_lines_with_document_id_from_anchor(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    _add_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER)

    inputs = collect_ledger_inputs(db, matter=matter)
    assert len(inputs.lines) == 1
    assert inputs.lines[0].document_id == doc.id
    assert inputs.lines[0].billed_cents == 10_000
    assert inputs.excluded_doc_ids == frozenset()


def test_superseded_decision_excludes_its_document(db: Session, matter: Matter) -> None:
    doc_a = _add_document(db, matter)
    doc_b = _add_document(db, matter, dedup_status=DedupStatus.DUPLICATE_OF)
    _add_line(db, matter, doc_a, billed=10_000, category=LedgerCategory.ER)
    _add_line(db, matter, doc_b, billed=99_999, category=LedgerCategory.SURGERY)
    _superseded_decision(db, matter, doc_b)

    inputs = collect_ledger_inputs(db, matter=matter)
    assert doc_b.id in inputs.excluded_doc_ids
    assert doc_a.id not in inputs.excluded_doc_ids


def test_duplicate_of_pending_is_excluded(db: Session, matter: Matter) -> None:
    doc_c = _add_document(db, matter, dedup_status=DedupStatus.DUPLICATE_OF)
    _add_line(db, matter, doc_c, billed=50_000, category=LedgerCategory.IMAGING)
    _pending_decision(db, matter, doc_c)

    inputs = collect_ledger_inputs(db, matter=matter)
    assert doc_c.id in inputs.excluded_doc_ids


def test_duplicate_of_with_no_decision_row_is_excluded(db: Session, matter: Matter) -> None:
    """An exact-duplicate doc with no decision yet must not sum (conservative)."""
    doc = _add_document(db, matter, dedup_status=DedupStatus.DUPLICATE_OF)
    _add_line(db, matter, doc, billed=50_000, category=LedgerCategory.IMAGING)

    inputs = collect_ledger_inputs(db, matter=matter)
    assert doc.id in inputs.excluded_doc_ids


def test_partial_overlap_document_is_included(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter, dedup_status=DedupStatus.PARTIAL_OVERLAP)
    _add_line(db, matter, doc, billed=7_000, category=LedgerCategory.PT_CHIRO)

    inputs = collect_ledger_inputs(db, matter=matter)
    assert doc.id not in inputs.excluded_doc_ids


def test_malformed_anchor_raises_typed_error(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter)
    line = _add_line(db, matter, doc, billed=1_000, category=LedgerCategory.ER, anchor={"page": 1})
    with pytest.raises(MalformedAnchor) as excinfo:
        collect_ledger_inputs(db, matter=matter)
    assert excinfo.value.line_id == line.id
    assert excinfo.value.diagnostic_kind == "billing_line_malformed_anchor"


# --------------------------------------------------------------------------------------
# compute_matter_ledger — composed, KEPT re-inclusion, basis from pack
# --------------------------------------------------------------------------------------


def test_compute_matter_ledger_composes_billed_basis_from_az_pack(
    db: Session, matter: Matter
) -> None:
    doc_a = _add_document(db, matter)
    doc_b = _add_document(db, matter, dedup_status=DedupStatus.DUPLICATE_OF)
    _add_line(db, matter, doc_a, billed=10_000, category=LedgerCategory.ER, paid=6_000)
    _add_line(db, matter, doc_b, billed=99_999, category=LedgerCategory.SURGERY)
    _superseded_decision(db, matter, doc_b)

    pack = load_pack("AZ")
    led = compute_matter_ledger(db, matter=matter, pack=pack)

    assert led.basis == "billed"
    assert led.grand_total.billed_cents == 10_000  # doc_b excluded
    assert led.demand_basis_total_cents == 10_000
    assert "surgery" not in led.by_category


def test_resolving_duplicate_kept_re_includes_on_recompute(db: Session, matter: Matter) -> None:
    doc = _add_document(db, matter, dedup_status=DedupStatus.DUPLICATE_OF)
    _add_line(db, matter, doc, billed=25_000, category=LedgerCategory.IMAGING)
    decision = _pending_decision(db, matter, doc)

    pack = load_pack("AZ")
    before = compute_matter_ledger(db, matter=matter, pack=pack)
    assert before.grand_total.billed_cents == 0  # PENDING duplicate excluded

    # Attorney resolves the decision KEPT — the doc re-enters the ledger on recompute.
    decision.resolution = DedupResolution.KEPT.value
    db.commit()

    after = compute_matter_ledger(db, matter=matter, pack=pack)
    assert after.grand_total.billed_cents == 25_000
    assert after.line_set_hash != before.line_set_hash
