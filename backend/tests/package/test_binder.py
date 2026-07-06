"""Exhibit binder tests — collation, Bates, index, bookmarks (M5 Wave B2).

Self-contained (own in-memory engine + firm/attorney/matter + local-disk storage, direct ORM),
matching ``tests/package/test_manifest.py``'s fixture style. Synthetic PDFs via
``tests/corpus/pdf_builders.build_text_pdf``; exhibit picks drive the M4 manifest we consume. No
PHI; no brain2 import.

Coverage: a blocked manifest raises BinderBlocked with reasons; a page beyond the source length
raises BinderPageMissing; continuous Bates across two exhibits (ranges abut, CP00001… format);
rebuild -> identical Bates ranges; the index page text contains filenames + ranges; outline
entries == exhibit count (pypdf read-back); byte determinism (the pinned metadata + file id make
the binder sha256-stable — asserted honestly as byte-stable, not merely content-stable).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import tempfile
import uuid
from collections.abc import Iterator

import pytest
from pypdf import PdfReader
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.storage import LocalDiskStorage
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    PhiDisposition,
    UserRole,
)
from app.models.orm import CaseDocument, Firm, Matter, User
from app.models.schemas import ExhibitPickRequest
from app.package import binder as binder_mod
from app.package import manifest as mani
from tests.corpus.pdf_builders import build_text_pdf

_BATES_PREFIX = "CP"


# --------------------------------------------------------------------------------------
# Fixtures
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
def storage() -> LocalDiskStorage:
    return LocalDiskStorage(tempfile.mkdtemp())


@pytest.fixture
def firm(db: Session) -> Firm:
    f = Firm(id=uuid.uuid4(), name="Test Firm")
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def attorney(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="atty@firm.test",
        display_name="Attorney",
        role=UserRole.ATTORNEY.value,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def matter(db: Session, firm: Firm) -> Matter:
    m_ = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Jane Doe",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.PACKAGE_ASSEMBLY.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m_)
    db.commit()
    return m_


def _add_document_with_pdf(
    db: Session,
    storage: LocalDiskStorage,
    matter: Matter,
    *,
    filename: str,
    page_texts: list[str],
    storage_key: str | None = None,
) -> CaseDocument:
    """Create a CaseDocument + store a real synthetic PDF at its storage_key."""
    key = storage_key if storage_key is not None else f"blobs/{uuid.uuid4()}.pdf"
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.BILL.value,
        source_label=filename,
        filename=filename,
        storage_key=key,
        page_count=len(page_texts),
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    db.commit()
    if key:
        storage.put(key, build_text_pdf(page_texts))
    return doc


def _cleared_pick(
    db: Session,
    matter: Matter,
    attorney: User,
    doc: CaseDocument,
    *,
    pages: list[int],
    sort_order: int,
) -> None:
    ex = mani.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=pages, sort_order=sort_order),
    )
    mani.set_phi_disposition(db, user=attorney, exhibit=ex, disposition=PhiDisposition.CLEARED)


# --------------------------------------------------------------------------------------
# Build-time gate + integrity
# --------------------------------------------------------------------------------------


def test_binder_blocked_manifest_raises_with_reasons(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    doc = _add_document_with_pdf(db, storage, matter, filename="bill.pdf", page_texts=["p1", "p2"])
    # A pick with includes but PHI left PENDING -> the manifest blocks.
    mani.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[1], sort_order=1),
    )
    manifest = mani.build_draft_manifest(db, matter=matter, mint_tokens=True)
    assert manifest.blocking  # sanity: it is blocked

    with pytest.raises(binder_mod.BinderBlocked) as excinfo:
        binder_mod.build_binder_pdf(
            db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
        )
    assert any("pending PHI" in r for r in excinfo.value.reasons)


def test_binder_page_beyond_source_length_raises(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    # page_count says 5 (pick passes the manifest), but the stored PDF only has 2 pages.
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.BILL.value,
        source_label="short.pdf",
        filename="short.pdf",
        storage_key="blobs/short.pdf",
        page_count=5,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    db.commit()
    storage.put("blobs/short.pdf", build_text_pdf(["only", "two"]))  # 2 real pages
    _cleared_pick(db, matter, attorney, doc, pages=[1, 4], sort_order=1)

    manifest = mani.build_draft_manifest(db, matter=matter, mint_tokens=True)
    with pytest.raises(binder_mod.BinderPageMissing) as excinfo:
        binder_mod.build_binder_pdf(
            db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
        )
    assert excinfo.value.page == 4
    assert str(excinfo.value.document_id) == str(doc.id)


def test_binder_missing_storage_key_raises_page_missing(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    # A document with no stored blob (storage_key NULL) -> BinderPageMissing on its first page.
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.BILL.value,
        source_label="nokey.pdf",
        filename="nokey.pdf",
        storage_key=None,
        page_count=3,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    db.commit()
    _cleared_pick(db, matter, attorney, doc, pages=[1, 2], sort_order=1)

    manifest = mani.build_draft_manifest(db, matter=matter, mint_tokens=True)
    with pytest.raises(binder_mod.BinderPageMissing) as excinfo:
        binder_mod.build_binder_pdf(
            db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
        )
    assert excinfo.value.page == 1


# --------------------------------------------------------------------------------------
# Bates continuity + index + bookmarks + determinism
# --------------------------------------------------------------------------------------


def _two_exhibit_manifest(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> mani.DraftBinderManifest:
    doc_a = _add_document_with_pdf(
        db, storage, matter, filename="alpha.pdf", page_texts=["a1", "a2"]
    )
    doc_b = _add_document_with_pdf(db, storage, matter, filename="bravo.pdf", page_texts=["b1"])
    _cleared_pick(db, matter, attorney, doc_a, pages=[1, 2], sort_order=1)
    _cleared_pick(db, matter, attorney, doc_b, pages=[1], sort_order=2)
    return mani.build_draft_manifest(db, matter=matter, mint_tokens=True)


def test_binder_bates_continuity_across_two_exhibits(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    manifest = _two_exhibit_manifest(db, storage, matter, attorney)
    _pdf, bates = binder_mod.build_binder_pdf(
        db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
    )
    # Two entries, ranges abut: exhibit A = 1..2, exhibit B = 3..3 (continuous, index unstamped).
    ranges = sorted(bates.values())
    assert ranges == [(1, 2), (3, 3)]
    # Exhibit A (alpha.pdf, first in manifest order) starts at CP00001.
    entries = manifest.entries
    a_id = str(entries[0].document_id)
    assert bates[a_id] == (1, 2)


def test_binder_rebuild_identical_bates_and_bytes(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    manifest = _two_exhibit_manifest(db, storage, matter, attorney)
    pdf1, bates1 = binder_mod.build_binder_pdf(
        db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
    )
    pdf2, bates2 = binder_mod.build_binder_pdf(
        db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
    )
    assert bates1 == bates2
    # Byte-stable: pinned pypdf metadata + file id + reportlab invariant overlays -> same sha256.
    assert hashlib.sha256(pdf1).hexdigest() == hashlib.sha256(pdf2).hexdigest()


def test_binder_index_page_lists_filenames_and_ranges(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    manifest = _two_exhibit_manifest(db, storage, matter, attorney)
    pdf, _bates = binder_mod.build_binder_pdf(
        db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
    )
    reader = PdfReader(io.BytesIO(pdf))
    index_text = reader.pages[0].extract_text() or ""
    assert "Exhibit Index" in index_text
    assert "alpha.pdf" in index_text
    assert "bravo.pdf" in index_text
    # Bates ranges printed with the CP prefix.
    assert "CP00001" in index_text
    assert "CP00003" in index_text


def test_binder_page_count_and_outline_entries(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    manifest = _two_exhibit_manifest(db, storage, matter, attorney)
    pdf, _bates = binder_mod.build_binder_pdf(
        db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
    )
    reader = PdfReader(io.BytesIO(pdf))
    # 1 index page + 2 (exhibit A) + 1 (exhibit B) = 4 pages.
    assert len(reader.pages) == 4
    # One outline entry per exhibit (bookmarks) — read back via pypdf.
    assert len(reader.outline) == 2


def test_binder_bates_label_format_is_five_digits(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    doc = _add_document_with_pdf(db, storage, matter, filename="one.pdf", page_texts=["p1"])
    _cleared_pick(db, matter, attorney, doc, pages=[1], sort_order=1)
    manifest = mani.build_draft_manifest(db, matter=matter, mint_tokens=True)
    pdf, bates = binder_mod.build_binder_pdf(
        db, storage, matter=matter, manifest=manifest, bates_prefix=_BATES_PREFIX
    )
    assert bates[str(doc.id)] == (1, 1)
    index_text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""
    assert "CP00001" in index_text
