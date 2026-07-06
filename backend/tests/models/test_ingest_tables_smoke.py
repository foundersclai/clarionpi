"""Smoke test for the M1 ingest tables: one row of each via ``tenant_add`` + the page uniq guard.

Builds the full schema on in-memory SQLite, inserts a firm/user/matter and one row of every
new table (``upload_sessions``, ``upload_slots``, ``page_texts``, ``dedup_decisions``) plus the
new columns on ``case_documents`` / ``document_pages``, all stamped through ``tenant_add`` so
the tenancy write door is exercised. Also asserts the ``(document_id, page_no)`` unique
constraint rejects a duplicate page (inv 2).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.tenancy import tenant_add
from app.models import enums, orm


@pytest.fixture
def session() -> Iterator[Session]:
    engine = sa.create_engine("sqlite://")
    orm.Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def _seed(session: Session) -> tuple[uuid.UUID, orm.Matter, orm.CaseDocument]:
    firm_id = uuid.uuid4()
    session.add(orm.Firm(id=firm_id, name="Acme Injury Law"))
    matter = orm.Matter(
        firm_id=firm_id,
        client_display_name="Jane Roe",
        claim_type=enums.ClaimType.MVA.value,
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=enums.GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    session.add(matter)
    session.flush()
    doc = orm.CaseDocument(
        firm_id=firm_id,
        matter_id=matter.id,
        doc_type=enums.DocType.BILL.value,
        source_label="upload",
        filename="bill.pdf",
        storage_key="matters/x/bill.pdf",
        page_count=1,
        dedup_status=enums.DedupStatus.UNIQUE.value,
        status=enums.DocStatus.CLASSIFIED.value,
        classification_confidence=0.92,
        needs_review=False,
    )
    session.add(doc)
    session.flush()
    return firm_id, matter, doc


def test_insert_one_row_of_each_new_table_via_tenant_add(session: Session) -> None:
    firm_id, matter, doc = _seed(session)

    upload_session = orm.UploadSession(
        matter_id=matter.id,
        status=enums.UploadSessionStatus.OPEN.value,
        ttl_expires_at=dt.datetime(2026, 7, 7, tzinfo=dt.UTC),
    )
    tenant_add(session, upload_session, firm_id)
    session.flush()

    slot = orm.UploadSlot(
        session_id=upload_session.id,
        filename="bill.pdf",
        size_bytes=2048,
        storage_key="matters/x/bill.pdf",
        received=True,
        document_id=doc.id,
    )
    tenant_add(session, slot, firm_id)

    page = orm.DocumentPage(
        firm_id=firm_id,
        document_id=doc.id,
        page_no=1,
        text="line item",
        text_source=enums.TextSource.TEXT_LAYER.value,
        zero_text=False,
    )
    session.add(page)
    session.flush()

    page_text = orm.PageText(
        page_id=page.id,
        text="line item",
        text_source=enums.TextSource.TEXT_LAYER.value,
        ocr_confidence=None,
        engine=None,
    )
    tenant_add(session, page_text, firm_id)
    session.flush()
    # Move the page's active text pointer at the plain-Uuid column (inv 2).
    page.active_text_id = page_text.id

    decision = orm.DedupDecision(
        matter_id=matter.id,
        document_id=doc.id,
        against_document_id=None,
        status=enums.DedupStatus.PARTIAL_OVERLAP.value,
        page_hash_matches=[[1, 1]],
        shingle_overlap=0.41,
        resolution=enums.DedupResolution.PENDING.value,
    )
    tenant_add(session, decision, firm_id)
    session.commit()

    assert session.query(orm.UploadSession).count() == 1
    assert session.query(orm.UploadSlot).count() == 1
    assert session.query(orm.PageText).count() == 1
    assert session.query(orm.DedupDecision).count() == 1
    reloaded = session.get(orm.DocumentPage, page.id)
    assert reloaded is not None
    assert reloaded.active_text_id == page_text.id


def test_document_page_unique_constraint_rejects_duplicate_page_no(session: Session) -> None:
    firm_id, _matter, doc = _seed(session)
    session.add(
        orm.DocumentPage(
            firm_id=firm_id,
            document_id=doc.id,
            page_no=1,
            text="first",
            text_source=enums.TextSource.NONE.value,
            zero_text=True,
        )
    )
    session.flush()
    session.add(
        orm.DocumentPage(
            firm_id=firm_id,
            document_id=doc.id,
            page_no=1,  # duplicate (document_id, page_no) — must be rejected
            text="dup",
            text_source=enums.TextSource.NONE.value,
            zero_text=True,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
