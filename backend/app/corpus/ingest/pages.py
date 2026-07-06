"""Per-page text pipeline (corpus_ingest §4 A4/A5).

The path from a stored PDF blob to immutable, page-addressable :class:`DocumentPage` rows:

1. **Text-layer fast path** — ``pdfplumber.extract_text()`` per page; if the text clears the
   char-density floor it wins (source ``TEXT_LAYER``), no OCR spend.
2. **OCR fallback** — a thin/absent text layer (scans, faxes) falls through to the
   :class:`OcrEngine`; the page carries the OCR text + confidence (source ``OCR``). No engine
   configured → the page is left with no text (``zero_text``, source ``NONE``).
3. **Immutable identity + append-only history** — each page's text columns are a denormalized
   MIRROR of its active :class:`PageText` version; a re-OCR appends a new ``PageText`` and
   moves ``active_text_id`` without ever touching ``page.id`` / ``(document_id, page_no)``
   (system_contract invariant 2). This module is the single writer of both.

Failures are typed onto the document (``status=FAILED`` + ``failure_reason``), never raised:
a 500-document batch must not die on a corrupt doc 3.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.storage import ObjectStorage, StoredObjectNotFound
from app.core.tenancy import tenant_add
from app.corpus.ocr import OcrEngine, OcrUnavailable
from app.models.enums import DocStatus, TextSource
from app.models.orm import CaseDocument, DocumentPage, PageText

# CaseDocument.failure_reason is String(512); truncate any reason to fit the column.
_FAILURE_REASON_MAX = 512


@dataclass(frozen=True)
class PageBuildOutcome:
    """The result of building one document's pages.

    ``ocr_fallbacks`` counts pages that went to the OCR engine; ``zero_text_pages`` counts
    pages left with no usable text (source ``NONE`` on refusal, or ``OCR`` with empty text).
    """

    pages_created: int
    ocr_fallbacks: int
    zero_text_pages: int
    failed: bool
    failure_reason: str | None


def density_ok(text: str | None, floor: int) -> bool:
    """Whitespace-normalized length of ``text`` meets ``floor`` (exposed for tests).

    A garbage-thin text layer (a fax with a few stray glyphs) falls *below* the floor and so
    routes to OCR by design — the floor is fixed at M1 (corpus_ingest §8 open question).
    """
    if not text:
        return False
    normalized = " ".join(text.split())
    return len(normalized) >= floor


def _mark_failed(db: Session, document: CaseDocument, reason: str) -> PageBuildOutcome:
    """Stamp the document FAILED with a truncated reason, commit, and return a failed outcome."""
    reason = reason[:_FAILURE_REASON_MAX]
    document.status = DocStatus.FAILED.value
    document.failure_reason = reason
    db.commit()
    return PageBuildOutcome(
        pages_created=0,
        ocr_fallbacks=0,
        zero_text_pages=0,
        failed=True,
        failure_reason=reason,
    )


def build_document_pages(
    db: Session,
    *,
    storage: ObjectStorage,
    ocr: OcrEngine,
    document: CaseDocument,
) -> PageBuildOutcome:
    """Build the immutable :class:`DocumentPage` rows for ``document`` from its stored blob.

    Re-entrant and idempotent: a crash-retry never duplicates or rewrites pages. Any bad
    document (missing blob, corrupt/encrypted PDF) is marked ``FAILED`` and returned, never
    raised — one poison document must not sink the batch.
    """
    settings = get_settings()

    # Idempotent re-entry guard: pages already built for this doc → do nothing (the unique
    # (document_id, page_no) constraint backs this against a racing retry).
    existing = db.execute(
        select(func.count())
        .select_from(DocumentPage)
        .where(DocumentPage.document_id == document.id)
    ).scalar_one()
    if existing:
        return PageBuildOutcome(0, 0, 0, False, None)

    # Fetch the blob. A doc whose ingest never stored one (or whose key vanished) is a typed
    # document failure, not an exception the caller has to catch.
    if document.storage_key is None:
        return _mark_failed(db, document, "blob_missing")
    try:
        pdf_bytes = storage.get(document.storage_key)
    except StoredObjectNotFound:
        return _mark_failed(db, document, "blob_missing")

    ocr_fallbacks = 0
    zero_text_pages = 0
    page_count = 0
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for index, page in enumerate(pdf.pages):
                page_no = index + 1
                raw = page.extract_text() or ""
                if density_ok(raw, settings.text_density_floor):
                    text: str = raw
                    source = TextSource.TEXT_LAYER
                    confidence: float | None = None
                    engine: str | None = None
                else:
                    try:
                        result = ocr.ocr_page(pdf_bytes, page_no)
                    except OcrUnavailable:
                        # No engine (or binary): leave the page with no text, source NONE.
                        text = ""
                        source = TextSource.NONE
                        confidence = None
                        engine = None
                    else:
                        text = result.text
                        source = TextSource.OCR
                        confidence = result.confidence
                        engine = type(ocr).__name__
                        ocr_fallbacks += 1

                # zero_text means "no usable text", independent of whether OCR ran: an OCR
                # result that came back empty is still a zero_text page (source stays OCR).
                zero_text = not text
                if zero_text:
                    zero_text_pages += 1

                _create_page(
                    db,
                    document=document,
                    page_no=page_no,
                    text=text,
                    source=source,
                    confidence=confidence,
                    engine=engine,
                    zero_text=zero_text,
                )
                page_count += 1
    except Exception as exc:  # corrupt / encrypted / zero-byte / mid-read failure
        return _mark_failed(db, document, f"unreadable_pdf: {type(exc).__name__}")

    document.page_count = page_count
    document.status = DocStatus.OCR_DONE.value
    db.commit()
    return PageBuildOutcome(
        pages_created=page_count,
        ocr_fallbacks=ocr_fallbacks,
        zero_text_pages=zero_text_pages,
        failed=False,
        failure_reason=None,
    )


def _create_page(
    db: Session,
    *,
    document: CaseDocument,
    page_no: int,
    text: str,
    source: TextSource,
    confidence: float | None,
    engine: str | None,
    zero_text: bool,
) -> DocumentPage:
    """Create a page + its v1 :class:`PageText`, wiring ``active_text_id`` to that version.

    The DocumentPage text columns (``text``/``text_source``/``ocr_confidence``) are a
    denormalized MIRROR of the active PageText; this module is the single writer that keeps
    the two in lockstep.
    """
    page = DocumentPage(
        document_id=document.id,
        page_no=page_no,
        text=text,
        text_source=source.value,
        ocr_confidence=confidence,
        image_ref=f"{document.storage_key}#page={page_no}",
        zero_text=zero_text,
    )
    tenant_add(db, page, document.firm_id)
    db.flush()  # assign page.id before the PageText FK references it

    page_text = PageText(
        page_id=page.id,
        text=text,
        text_source=source.value,
        ocr_confidence=confidence,
        engine=engine,
    )
    tenant_add(db, page_text, document.firm_id)
    db.flush()  # assign page_text.id before pointing active_text_id at it

    page.active_text_id = page_text.id
    return page


def append_text_version(
    db: Session,
    *,
    page: DocumentPage,
    text: str,
    source: TextSource,
    confidence: float | None,
    engine: str | None,
) -> PageText:
    """Append a new :class:`PageText`, move ``active_text_id``, and refresh the mirror columns.

    The re-OCR path (system_contract invariant 2): page identity is stable and history is
    append-only. This MUST NOT touch ``page.id``, ``page.page_no``, ``page.image_ref``, or
    ``page.document_id`` — only the active pointer and the denormalized mirror move.
    """
    page_text = PageText(
        page_id=page.id,
        text=text,
        text_source=source.value,
        ocr_confidence=confidence,
        engine=engine,
    )
    tenant_add(db, page_text, page.firm_id)
    db.flush()  # assign page_text.id before pointing active_text_id at it

    page.active_text_id = page_text.id
    # Keep the denormalized mirror in lockstep with the new active version.
    page.text = text
    page.text_source = source.value
    page.ocr_confidence = confidence
    page.zero_text = not text
    db.commit()
    return page_text
