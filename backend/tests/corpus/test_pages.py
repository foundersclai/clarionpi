"""Unit tests for the per-page text pipeline (``app.corpus.ingest.pages``).

Deterministic and offline: synthetic PDFs from ``pdf_builders`` + the deterministic
:class:`FakeOcr` / :class:`NullOcr` engines — no network, no tesseract binary. The immutable
page-identity invariant (system_contract 2) is the load-bearing property here, so it is
checked directly *and* via a hypothesis property test.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.corpus.ingest.pages import (
    PageBuildOutcome,
    append_text_version,
    build_document_pages,
    density_ok,
)
from app.corpus.ocr import FakeOcr, NullOcr
from app.models.enums import DedupStatus, DocStatus, DocType, TextSource
from app.models.orm import CaseDocument, DocumentPage, PageText, User

from .pdf_builders import CORRUPT_PDF_BYTES, build_imageonly_pdf, build_text_pdf


def _make_document(
    db: Session,
    user: User,
    matter_id: uuid.UUID,
    storage: LocalDiskStorage,
    pdf_bytes: bytes | None,
    *,
    store: bool = True,
) -> CaseDocument:
    """Store ``pdf_bytes`` (when ``store``) and create an UPLOADED CaseDocument for it.

    ``store=False`` (or ``pdf_bytes=None``) leaves ``storage_key`` pointing at a blob that was
    never written — the missing-blob failure case.
    """
    key = f"matters/{matter_id}/{uuid.uuid4()}.pdf"
    if store and pdf_bytes is not None:
        storage.put(key, pdf_bytes)
    doc = CaseDocument(
        matter_id=matter_id,
        doc_type=DocType.MEDICAL_RECORD.value,
        source_label="test upload",
        filename="record.pdf",
        storage_key=key,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.UPLOADED.value,
    )
    tenant_add(db, doc, user.firm_id)
    db.commit()
    return doc


def _pages(db: Session, doc: CaseDocument) -> list[DocumentPage]:
    return list(
        db.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == doc.id)
            .order_by(DocumentPage.page_no)
        ).scalars()
    )


def _text_versions(db: Session, page: DocumentPage) -> list[PageText]:
    return list(
        db.execute(
            select(PageText)
            .where(PageText.page_id == page.id)
            .order_by(PageText.created_at, PageText.id)
        ).scalars()
    )


# --------------------------------------------------------------------------------------
# density_ok edge cases
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "floor", "expected"),
    [
        (None, 32, False),
        ("", 32, False),
        ("        \n\t  ", 32, False),  # whitespace-only normalizes to empty
        ("abcd", 4, True),  # exactly floor length
        ("abc", 4, False),  # one under floor
        ("  a b c d  ", 7, True),  # normalized "a b c d" is 7 chars
    ],
)
def test_density_ok_edge_cases(text: str | None, floor: int, expected: bool) -> None:
    assert density_ok(text, floor) is expected


# --------------------------------------------------------------------------------------
# build_document_pages
# --------------------------------------------------------------------------------------


def test_text_layer_fast_path(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    pdf = build_text_pdf(
        [
            "Page one has a real, extractable text layer well over the density floor here.",
            "Second page likewise carries plenty of readable characters for the extractor.",
            "Third and final page rounds out a clean three-page text document nicely today.",
        ]
    )
    doc = _make_document(db, dev_user, matter.id, storage, pdf)

    outcome = build_document_pages(db, storage=storage, ocr=NullOcr(), document=doc)

    assert outcome == PageBuildOutcome(3, 0, 0, False, None)
    db.refresh(doc)
    assert doc.page_count == 3
    assert doc.status == DocStatus.OCR_DONE.value

    pages = _pages(db, doc)
    assert [p.page_no for p in pages] == [1, 2, 3]
    for p in pages:
        assert p.text_source == TextSource.TEXT_LAYER.value
        assert p.text.strip()  # mirror text non-empty
        assert p.ocr_confidence is None
        assert p.zero_text is False
        assert p.image_ref == f"{doc.storage_key}#page={p.page_no}"
        versions = _text_versions(db, p)
        assert len(versions) == 1
        assert p.active_text_id == versions[0].id
        assert versions[0].engine is None  # text-layer has no OCR engine


def test_density_floor_routes_to_ocr(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    doc = _make_document(db, dev_user, matter.id, storage, build_imageonly_pdf(2))
    ocr = FakeOcr(default_text="ocr text")

    outcome = build_document_pages(db, storage=storage, ocr=ocr, document=doc)

    assert outcome == PageBuildOutcome(2, 2, 0, False, None)
    assert ocr.calls == [1, 2]  # OCR fired for every image-only page
    for p in _pages(db, doc):
        assert p.text_source == TextSource.OCR.value
        assert p.text == "ocr text"
        assert p.ocr_confidence == 0.88
        assert p.zero_text is False
        version = _text_versions(db, p)[0]
        assert version.engine == "FakeOcr"
        assert version.text_source == TextSource.OCR.value


def test_zero_text_no_engine(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    doc = _make_document(db, dev_user, matter.id, storage, build_imageonly_pdf(1))

    outcome = build_document_pages(db, storage=storage, ocr=NullOcr(), document=doc)

    assert outcome == PageBuildOutcome(1, 0, 1, False, None)
    (page,) = _pages(db, doc)
    assert page.text_source == TextSource.NONE.value
    assert page.text == ""
    assert page.zero_text is True
    assert page.ocr_confidence is None


def test_zero_text_empty_ocr_result(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    # OCR ran but produced empty text → still a zero_text page, but source stays OCR.
    doc = _make_document(db, dev_user, matter.id, storage, build_imageonly_pdf(1))
    ocr = FakeOcr(default_text="")

    outcome = build_document_pages(db, storage=storage, ocr=ocr, document=doc)

    assert outcome == PageBuildOutcome(1, 1, 1, False, None)
    (page,) = _pages(db, doc)
    assert page.text_source == TextSource.OCR.value
    assert page.text == ""
    assert page.zero_text is True
    assert page.ocr_confidence == 0.88  # confidence kept even though text is empty


def test_mixed_document_blank_page_falls_to_ocr(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    # Page 1 has a real text layer; page 2 is blank (empty string) → OCR fallback.
    pdf = build_text_pdf(["Real medical narrative text with more than enough characters here.", ""])
    doc = _make_document(db, dev_user, matter.id, storage, pdf)
    ocr = FakeOcr(default_text="ocr for blank page")

    outcome = build_document_pages(db, storage=storage, ocr=ocr, document=doc)

    assert outcome == PageBuildOutcome(2, 1, 0, False, None)
    assert ocr.calls == [2]  # only the blank page went to OCR
    p1, p2 = _pages(db, doc)
    assert p1.text_source == TextSource.TEXT_LAYER.value
    assert p2.text_source == TextSource.OCR.value
    assert p2.text == "ocr for blank page"


def test_corrupt_pdf_marks_failed(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    doc = _make_document(db, dev_user, matter.id, storage, CORRUPT_PDF_BYTES)

    outcome = build_document_pages(db, storage=storage, ocr=NullOcr(), document=doc)

    assert outcome.failed is True
    assert outcome.pages_created == 0
    assert outcome.failure_reason is not None
    assert outcome.failure_reason.startswith("unreadable_pdf:")
    db.refresh(doc)
    assert doc.status == DocStatus.FAILED.value
    assert doc.failure_reason == outcome.failure_reason
    assert _pages(db, doc) == []


def test_missing_blob_marks_failed(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    # storage_key is set on the doc but nothing was ever written there.
    doc = _make_document(db, dev_user, matter.id, storage, build_text_pdf(["x"]), store=False)

    outcome = build_document_pages(db, storage=storage, ocr=NullOcr(), document=doc)

    assert outcome == PageBuildOutcome(0, 0, 0, True, "blob_missing")
    db.refresh(doc)
    assert doc.status == DocStatus.FAILED.value
    assert doc.failure_reason == "blob_missing"
    assert _pages(db, doc) == []


def test_idempotent_reentry(db: Session, dev_user: User, matter, storage: LocalDiskStorage) -> None:
    pdf = build_text_pdf(["First page text well past the density floor for a clean pass here."])
    doc = _make_document(db, dev_user, matter.id, storage, pdf)

    first = build_document_pages(db, storage=storage, ocr=NullOcr(), document=doc)
    assert first.pages_created == 1

    pages_before = _pages(db, doc)
    ids_before = [p.id for p in pages_before]
    active_before = [p.active_text_id for p in pages_before]

    # Second call is a no-op: no duplicate/rewritten rows.
    second = build_document_pages(db, storage=storage, ocr=NullOcr(), document=doc)
    assert second == PageBuildOutcome(0, 0, 0, False, None)

    pages_after = _pages(db, doc)
    assert [p.id for p in pages_after] == ids_before
    assert [p.active_text_id for p in pages_after] == active_before
    total_versions = db.execute(
        select(func.count()).select_from(PageText).where(PageText.page_id == ids_before[0])
    ).scalar_one()
    assert total_versions == 1


# --------------------------------------------------------------------------------------
# append_text_version — the re-OCR path (invariant 2)
# --------------------------------------------------------------------------------------


def test_append_text_version_moves_active_and_preserves_identity(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    doc = _make_document(db, dev_user, matter.id, storage, build_imageonly_pdf(1))
    build_document_pages(db, storage=storage, ocr=FakeOcr(default_text="first pass"), document=doc)
    (page,) = _pages(db, doc)

    page_id_before = page.id
    page_no_before = page.page_no
    image_ref_before = page.image_ref
    doc_id_before = page.document_id
    v1_id = page.active_text_id

    new_version = append_text_version(
        db,
        page=page,
        text="re-ocr improved text",
        source=TextSource.OCR,
        confidence=0.95,
        engine="TesseractOcr",
    )

    # History appended, active pointer moved to the new version.
    versions = _text_versions(db, page)
    assert len(versions) == 2
    assert page.active_text_id == new_version.id
    assert new_version.id != v1_id

    # Mirror updated to the new active version.
    assert page.text == "re-ocr improved text"
    assert page.text_source == TextSource.OCR.value
    assert page.ocr_confidence == 0.95
    assert page.zero_text is False

    # Identity UNCHANGED (invariant 2).
    assert page.id == page_id_before
    assert page.page_no == page_no_before
    assert page.image_ref == image_ref_before
    assert page.document_id == doc_id_before


def test_append_empty_text_version_sets_zero_text(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage
) -> None:
    doc = _make_document(db, dev_user, matter.id, storage, build_imageonly_pdf(1))
    build_document_pages(db, storage=storage, ocr=FakeOcr(default_text="had text"), document=doc)
    (page,) = _pages(db, doc)
    assert page.zero_text is False

    append_text_version(
        db, page=page, text="", source=TextSource.NONE, confidence=None, engine=None
    )

    assert page.text == ""
    assert page.zero_text is True
    assert page.text_source == TextSource.NONE.value
    assert len(_text_versions(db, page)) == 2


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(texts=st.lists(st.text(max_size=40), min_size=1, max_size=6))
def test_append_history_property(
    db: Session, dev_user: User, matter, storage: LocalDiskStorage, texts: list[str]
) -> None:
    """Any sequence of appends keeps identity fixed and history append-only (invariant 2)."""
    doc = _make_document(db, dev_user, matter.id, storage, build_imageonly_pdf(1))
    build_document_pages(db, storage=storage, ocr=FakeOcr(default_text="seed"), document=doc)
    (page,) = _pages(db, doc)

    page_id = page.id
    page_no = page.page_no
    image_ref = page.image_ref

    for step, text in enumerate(texts, start=1):
        version = append_text_version(
            db,
            page=page,
            text=text,
            source=TextSource.OCR,
            confidence=0.5,
            engine="FakeOcr",
        )
        # After every step: identity constant, history == 1 seed + `step` appends.
        assert page.id == page_id
        assert page.page_no == page_no
        assert page.image_ref == image_ref
        assert len(_text_versions(db, page)) == 1 + step
        assert page.active_text_id == version.id
        assert page.text == text
