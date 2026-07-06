"""M1 EXIT CRITERION — Phase 0 end-to-end at scale (``@pytest.mark.integration``).

Excluded from the fast suite (``make test`` runs ``-m "not integration"``); run it explicitly:

    .venv/bin/pytest -m integration tests/corpus/test_phase0_integration.py

This is the M1 acceptance run: a realistically large corpus (a 500-page text document, an
exact-duplicate 3-page pair, and an image-only 2-page document) processed to exhaustion through
:func:`~app.corpus.ingest.phase0.run_phase0`, asserting the provenance floor holds at scale:

* 505 total :class:`DocumentPage` rows across the four documents;
* the 500-pager's pages ALL carry non-empty ``text_layer`` text (the ≥0.98 M1 coverage number,
  asserted as the actual ratio even though the synthetic PDF satisfies it trivially);
* every page's ``image_ref`` is ``{storage_key}#page={n}`` and its ``active_text_id`` resolves to
  a real :class:`PageText` row (provenance intact);
* the duplicate is quarantined with a PENDING :class:`DedupDecision`;
* the image-only document's pages are counted as ``zero_text``;
* the matter reaches ``facts_review`` and the run log records ``run_completed``.

Still deterministic and offline: synthetic PDFs, :class:`FakeOcr`, :class:`ScriptedProvider`
(so one real classify parse path runs for each document, including the 500-pager). Kept well
under ~90s; the wall time is printed.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.matter_logs import MatterRunLogger
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.corpus.ingest.phase0 import run_phase0
from app.corpus.ocr import FakeOcr
from app.models.enums import DedupResolution, DedupStatus, DocStatus, DocType, GateState, TextSource
from app.models.orm import CaseDocument, DedupDecision, DocumentPage, Matter, PageText, User

from .pdf_builders import build_imageonly_pdf, build_text_pdf

_CLASSIFY_JSON = '{"doc_type": "medical_record", "confidence": 0.95, "rationale": "r"}'

_BIG_DOC_PAGES = 500


def _classify_result() -> CompletionResult:
    return CompletionResult(text=_CLASSIFY_JSON, input_tokens=10, output_tokens=5, cost_cents=1)


def _make_doc(
    db: Session,
    user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    pdf_bytes: bytes,
    filename: str,
) -> CaseDocument:
    key = f"matters/{matter.id}/{uuid.uuid4()}.pdf"
    storage.put(key, pdf_bytes)
    doc = CaseDocument(
        matter_id=matter.id,
        doc_type=DocType.OTHER.value,
        source_label=filename,
        filename=filename,
        storage_key=key,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.UPLOADED.value,
    )
    tenant_add(db, doc, user.firm_id)
    db.commit()
    return doc


@pytest.mark.integration
def test_m1_exit_phase0_at_scale(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # 500-page text doc: vary each page so dedup never false-positives on it.
    big_pages = [
        f"Progress note page {i}: patient reports improvement. Visit {i} of the treatment course."
        for i in range(1, _BIG_DOC_PAGES + 1)
    ]
    big_doc = _make_doc(db, dev_user, matter, storage, build_text_pdf(big_pages), "records_500.pdf")

    # An exact-duplicate 3-page pair (same content).
    dup_pages = [
        f"Duplicate exhibit page {i}: identical across both filed copies." for i in range(1, 4)
    ]
    dup_a = _make_doc(db, dev_user, matter, storage, build_text_pdf(dup_pages), "dup_one.pdf")
    dup_b = _make_doc(db, dev_user, matter, storage, build_text_pdf(dup_pages), "dup_two.pdf")
    earlier, later = sorted((dup_a, dup_b), key=lambda d: (d.created_at, d.id))

    # An image-only 2-page doc: no text layer, FakeOcr default is empty -> zero_text pages.
    img_doc = _make_doc(db, dev_user, matter, storage, build_imageonly_pdf(2), "photos.pdf")

    provider = ScriptedProvider([_classify_result() for _ in range(4)])
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    start = time.perf_counter()
    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),  # default_text="" -> image-only pages stay zero_text via OCR
            provider=provider,
            run_logger=logger,
        )
    )
    elapsed = time.perf_counter() - start
    print(f"\n[M1-exit] run_phase0 over {_BIG_DOC_PAGES + 3 + 3 + 2} pages took {elapsed:.2f}s")

    assert frames, "run must emit frames"
    assert elapsed < 90.0, f"M1-exit run too slow: {elapsed:.2f}s"

    # ---- Total page rows == 505 across all four documents -------------------------------
    total_pages = db.scalar(select(func.count()).select_from(DocumentPage))
    assert total_pages == _BIG_DOC_PAGES + 3 + 3 + 2  # 505

    # ---- The 500-pager: all pages non-empty text_layer; coverage ratio >= 0.98 ----------
    big_page_rows = list(
        db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == big_doc.id)
            .order_by(DocumentPage.page_no)
        )
    )
    assert len(big_page_rows) == _BIG_DOC_PAGES
    text_layer_pages = [p for p in big_page_rows if p.text_source == TextSource.TEXT_LAYER.value]
    coverage = len(text_layer_pages) / _BIG_DOC_PAGES
    assert coverage >= 0.98, f"text-layer coverage {coverage:.3f} below the 0.98 M1 number"
    for p in big_page_rows:
        assert p.text_source == TextSource.TEXT_LAYER.value
        assert p.text.strip(), f"page {p.page_no} had empty text"
        assert p.zero_text is False

    # ---- Provenance intact: image_ref shape + active_text_id resolves to a PageText -----
    for p in big_page_rows:
        assert p.image_ref == f"{big_doc.storage_key}#page={p.page_no}"
        assert p.active_text_id is not None
        page_text = db.get(PageText, p.active_text_id)
        assert page_text is not None
        assert page_text.page_id == p.id

    # ---- Duplicate quarantined with a PENDING decision ----------------------------------
    db.refresh(later)
    assert later.dedup_status == DedupStatus.DUPLICATE_OF.value
    decision = db.scalars(select(DedupDecision).where(DedupDecision.document_id == later.id)).one()
    assert decision.against_document_id == earlier.id
    assert decision.resolution == DedupResolution.PENDING.value

    # ---- Image-only doc: pages counted as zero_text -------------------------------------
    img_page_rows = list(
        db.scalars(select(DocumentPage).where(DocumentPage.document_id == img_doc.id))
    )
    assert len(img_page_rows) == 2
    assert all(p.zero_text for p in img_page_rows)

    # ---- Matter reached facts_review; run log records completion -------------------------
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value
    log_events = [
        line.split('"event": "', 1)[1].split('"', 1)[0]
        for line in logger.path.read_text(encoding="utf-8").splitlines()
        if '"event": "' in line
    ]
    assert "run_completed" in log_events
