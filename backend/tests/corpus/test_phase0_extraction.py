"""Tests for the Phase-0 extraction + sync stage (``app.corpus.ingest.phase0``, M2 Wave C2).

These exercise what Wave C2 adds on top of the M1 classify->pages->dedup run: the per-doc
extraction stage, the post-loop sync stage (encounter merge -> registry sync -> ledger AMT mint),
the widened re-entrancy that resumes an ``ocr_done`` doc extraction-only, and the reclassify-then-
rerun path.

Deterministic and service-free, like ``test_phase0.py``: synthetic PDFs from ``pdf_builders``, the
deterministic :class:`FakeOcr`, and :class:`ScriptedProvider` / :class:`NullProvider`. The
**ScriptedProvider script order is load-bearing** — for a fresh ``uploaded`` doc the runner makes
one classify call THEN one extractor call per window (a doc with fewer pages than the window size
is a single window), and docs are processed in ``(created_at, id)`` order. Get the script order
wrong and the provider raises ``script exhausted`` at the wrong stage.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.llm_provider import CompletionResult, NullProvider, ScriptedProvider
from app.core.matter_logs import MatterRunLogger
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.corpus.ingest.classify import reclassify_document
from app.corpus.ingest.phase0 import run_phase0
from app.corpus.ocr import FakeOcr
from app.models.enums import DedupStatus, DocStatus, DocType, GateState, SseEvent, TokenKind
from app.models.orm import (
    BillingLine,
    CaseDocument,
    ExtractionRun,
    FactToken,
    LlmCall,
    Matter,
    MedicalEncounter,
    User,
)

from .pdf_builders import build_text_pdf

# --------------------------------------------------------------------------------------
# Scripted model replies (classify JSONs, then extraction JSONs, in call order)
# --------------------------------------------------------------------------------------


def _result(text: str) -> CompletionResult:
    """One scripted model reply with nominal token/cost accounting."""
    return CompletionResult(text=text, input_tokens=20, output_tokens=10, cost_cents=1)


def _classify(doc_type: str) -> CompletionResult:
    """A scripted, above-floor classify verdict for ``doc_type``."""
    return _result(json.dumps({"doc_type": doc_type, "confidence": 0.95, "rationale": "r"}))


def _encounter(
    page: int, *, provider: str = "Dr. Smith", dos: str = "2026-02-01"
) -> CompletionResult:
    """A scripted medical-extractor window reply: one encounter anchored to ``page``."""
    return _result(
        json.dumps(
            {
                "encounters": [
                    {
                        "date_of_service": dos,
                        "provider": provider,
                        "facility": "Mercy General",
                        "encounter_type": "office visit",
                        "complaints": ["neck pain"],
                        "findings": ["tenderness"],
                        "diagnoses": ["strain"],
                        "procedures": [],
                        "work_status": "light duty",
                        "anchor_pages": [page],
                        "field_confidence": {"provider": 0.9, "date_of_service": 0.8},
                    }
                ]
            }
        )
    )


def _bill(page: int, *, billed: str = "$1,000.00", category: str = "imaging") -> CompletionResult:
    """A scripted bill-extractor window reply: one line anchored to ``page``."""
    return _result(
        json.dumps(
            {
                "lines": [
                    {
                        "provider": "Imaging Co",
                        "date_of_service": "2026-02-01",
                        "code": "70450",
                        "billed": billed,
                        "adjusted": None,
                        "paid": None,
                        "outstanding": None,
                        "category": category,
                        "anchor_page": page,
                    }
                ]
            }
        )
    )


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _make_doc(
    db: Session,
    user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    pdf_bytes: bytes,
    *,
    filename: str,
) -> CaseDocument:
    """Store ``pdf_bytes`` and create an UPLOADED, doc_type=other, unique CaseDocument for it."""
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


def _pages(n: int, tag: str) -> list[str]:
    """``n`` distinct dense text pages salted by ``tag`` (so two docs never collide in dedup)."""
    return [
        f"Progress note {tag} page {i}: patient reports improvement over prior visit number {i}."
        for i in range(1, n + 1)
    ]


def _parse_frames(frames: list[str]) -> list[tuple[str, dict]]:
    """Parse SSE frame strings into ``[(event_name, data), ...]``, asserting each frame's shape."""
    valid_events = {e.value for e in SseEvent}
    parsed: list[tuple[str, dict]] = []
    for frame in frames:
        assert frame.endswith("\n\n"), frame
        lines = frame[:-2].split("\n")
        assert len(lines) == 2, frame
        assert lines[0].startswith("event: "), frame
        assert lines[1].startswith("data: "), frame
        event_name = lines[0][len("event: ") :]
        assert event_name in valid_events, event_name
        parsed.append((event_name, json.loads(lines[1][len("data: ") :])))
    return parsed


def _log_events(logger: MatterRunLogger) -> list[str]:
    """The ``event`` field of every JSON line in the run log, in order."""
    lines = logger.path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["event"] for line in lines if line.strip()]


def _llm_stages(db: Session, matter: Matter) -> list[str]:
    """Every metered call's stage for the matter, in insertion order (the call sequence)."""
    return list(
        db.scalars(
            select(LlmCall.stage)
            .where(LlmCall.matter_id == matter.id)
            .order_by(LlmCall.created_at, LlmCall.id)
        )
    )


# --------------------------------------------------------------------------------------
# Happy path — one medical doc + one bill doc, full extract + merge + registry + ledger
# --------------------------------------------------------------------------------------


def test_happy_path_medical_and_bill_extract_merge_sync(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # doc_med sorts before doc_bill by (created_at, id)? created_at is second-resolution on SQLite,
    # so id is the tiebreaker — do NOT assume order. Script by matching each doc's classify reply to
    # its type; the extractor replies follow each classify in the SAME per-doc order the loop uses.
    # To make the order deterministic, create the medical doc first and assert the loop order below.
    doc_med = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_pages(1, "MED")), filename="m.pdf"
    )
    doc_bill = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_pages(1, "BILL")), filename="b.pdf"
    )
    first, second = sorted((doc_med, doc_bill), key=lambda d: (d.created_at, d.id))

    # Build the script in loop order: for each doc, its classify then its single-window extractor.
    def _script_for(doc: CaseDocument) -> list[CompletionResult]:
        if doc.id == doc_med.id:
            return [_classify("medical_record"), _encounter(1)]
        return [_classify("bill"), _bill(1)]

    provider = ScriptedProvider(_script_for(first) + _script_for(second))
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=provider,
            run_logger=logger,
        )
    )
    parsed = _parse_frames(frames)
    events = [name for name, _ in parsed]

    # Per-doc frames include an `extracting` then an `extracted` DOC_STATE for each; the sync stage
    # emits encounters_merged + registry_synced; the gate advances.
    doc_states = [
        (d["document_id"], d["status"])
        for name, d in parsed
        if name == SseEvent.DOC_STATE.value and d.get("status") in ("extracting", "extracted")
    ]
    for doc in (doc_med, doc_bill):
        seq = [status for did, status in doc_states if did == str(doc.id)]
        assert seq == ["extracting", "extracted"], seq  # extracting precedes extracted per doc
    assert SseEvent.GATE_READY.value in events

    states = [d.get("state") for name, d in parsed if name == SseEvent.STATUS.value]
    assert states == ["started", "encounters_merged", "registry_synced", "completed"]

    # Both docs reached EXTRACTED.
    for doc in (doc_med, doc_bill):
        db.refresh(doc)
        assert doc.status == DocStatus.EXTRACTED.value

    # The encounter + billing line persisted.
    encounters = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).all()
    assert len(encounters) == 1
    bills = db.query(BillingLine).filter(BillingLine.matter_id == matter.id).all()
    assert len(bills) == 1
    assert bills[0].billed_cents == 100000  # $1,000.00 -> exact cents, no float

    # Registry synced: a FACT token (the encounter) and AMT tokens (the ledger) exist; version >= 1.
    db.refresh(matter)
    assert matter.registry_version >= 1
    kinds = {t.kind for t in db.query(FactToken).filter(FactToken.matter_id == matter.id)}
    assert TokenKind.FACT.value in kinds
    assert TokenKind.AMOUNT.value in kinds

    # The ledger AMT for grand billed matches the scripted bill cents EXACTLY (ledger arithmetic is
    # pure code; the registry only stores the snapshot).
    grand_billed = (
        db.query(FactToken)
        .filter(
            FactToken.matter_id == matter.id,
            FactToken.source_ref == "amt:specials.grand.billed",
        )
        .order_by(FactToken.registry_version.desc())
        .first()
    )
    assert grand_billed is not None
    assert grand_billed.snapshot_value_cents == 100000

    # Gate advanced; summary fields correct.
    assert matter.gate_state == GateState.FACTS_REVIEW.value
    completed = parsed[-1][1]
    assert completed["state"] == "completed"
    assert completed["documents_processed"] == 2
    assert completed["documents_extracted"] == 2
    assert completed["extraction_rows"] == 2  # 1 encounter + 1 billing line
    assert completed["anchors_rejected"] == 0
    assert completed["facts_minted"] >= 1
    assert completed["amounts_minted"] >= 1
    assert completed["registry_version"] == matter.registry_version

    # Run log carries the new events.
    log_events = _log_events(logger)
    for expected in (
        "doc_extracted",
        "encounters_merged",
        "registry_synced",
        "ledger_amounts_minted",
    ):
        assert expected in log_events, f"missing {expected!r} in {log_events}"


# --------------------------------------------------------------------------------------
# Provider-unavailable degrade — the M1 no-LLM philosophy preserved
# --------------------------------------------------------------------------------------


def test_provider_unavailable_degrades_and_skips_extraction(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # NullProvider raises on every call -> classify degrades to `other` + review -> extraction is
    # skipped (doc_type_not_extractable) -> the run completes and the gate advances anyway.
    doc = _make_doc(db, dev_user, matter, storage, build_text_pdf(_pages(2, "N")), filename="n.pdf")
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=NullProvider(),
            run_logger=logger,
        )
    )
    parsed = _parse_frames(frames)

    db.refresh(doc)
    assert doc.doc_type == DocType.OTHER.value
    assert doc.needs_review is True
    assert doc.status == DocStatus.OCR_DONE.value  # paged, but not extractable -> not `extracted`

    # No extraction DOC_STATE frame; the skip is logged, not framed.
    assert not any(
        name == SseEvent.DOC_STATE.value
        and d.get("status") in ("extracted", "extraction_incomplete")
        for name, d in parsed
    )
    assert "doc_extraction_skipped" in _log_events(logger)

    # Gate advanced; extraction summary fields all zero (registry still mints its always-on AMTs).
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value
    completed = parsed[-1][1]
    assert completed["documents_extracted"] == 0
    assert completed["extraction_rows"] == 0
    assert completed["anchors_rejected"] == 0
    assert completed["encounters_merged"] == 0
    assert completed["facts_minted"] == 0


# --------------------------------------------------------------------------------------
# Resume — provider dies mid-extraction, re-POST resumes extraction-only (no re-classify)
# --------------------------------------------------------------------------------------


def test_resume_extraction_only_after_provider_dies_mid_run(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # A 10-page medical doc -> windows [1-8],[3-10] (two windows). First run: classify OK, then the
    # extractor script is exhausted mid-extraction (no window replies) -> window 1 records FAILED,
    # the runner stops, the doc stays OCR_DONE.
    doc = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_pages(10, "R")), filename="r.pdf"
    )
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    frames1 = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_classify("medical_record")]),  # no extractor replies
            run_logger=logger,
        )
    )
    parsed1 = _parse_frames(frames1)

    db.refresh(doc)
    assert doc.status == DocStatus.OCR_DONE.value  # stayed OCR_DONE (extraction incomplete)
    incomplete = next(
        d
        for name, d in parsed1
        if name == SseEvent.DOC_STATE.value and d.get("status") == "extraction_incomplete"
    )
    assert incomplete["document_id"] == str(doc.id)
    assert incomplete["error"] == "provider_unavailable"
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value  # gate advanced once (first run)

    stages_after_run1 = _llm_stages(db, matter)
    assert stages_after_run1.count("phase0.classify") == 1  # classified exactly once

    # Re-POST with a fresh, full extractor script (two windows). The OCR_DONE doc is picked up
    # extraction-ONLY: no second classify. Window 1 already has a FAILED run -> deleted + retried;
    # both windows now succeed -> doc reaches EXTRACTED.
    frames2 = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider(
                [_encounter(2), _encounter(9, provider="Dr. Jones", dos="2026-03-01")]
            ),
            run_logger=logger,
        )
    )
    parsed2 = _parse_frames(frames2)

    db.refresh(doc)
    assert doc.status == DocStatus.EXTRACTED.value
    extracted = next(
        d
        for name, d in parsed2
        if name == SseEvent.DOC_STATE.value and d.get("status") == "extracted"
    )
    assert extracted["document_id"] == str(doc.id)

    # No re-classify on the resume run: classify count is still exactly 1 across both runs — the
    # OCR_DONE doc skipped the classify stage entirely on the resume (the point of the widened
    # selection). This is the load-bearing assertion.
    assert _llm_stages(db, matter).count("phase0.classify") == 1
    # Extraction call ledger is cumulative: run 1 recorded window-1's attempt (a zero-cost row the
    # meter keeps even though the provider then raised on script exhaustion), and run 2 ran both
    # windows fresh — 1 + 2 = 3 metered extract calls total.
    assert _llm_stages(db, matter).count("extract.medical") == 3

    # The gate advanced exactly once total: the resume run is a late-documents run (no second
    # GATE_READY).
    assert not any(name == SseEvent.GATE_READY.value for name, _ in parsed2)
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value


# --------------------------------------------------------------------------------------
# Reclassify-then-rerun — an `other` doc reclassified to medical extracts on the next run
# --------------------------------------------------------------------------------------


def test_reclassify_other_to_medical_then_rerun_extracts(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # First run: the doc classifies `other` (a degraded/low-signal verdict), extraction skips, doc
    # ends OCR_DONE, gate advances to facts_review.
    doc = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_pages(1, "RC")), filename="rc.pdf"
    )
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_classify("other")]),
            run_logger=logger,
        )
    )
    db.refresh(doc)
    assert doc.status == DocStatus.OCR_DONE.value
    assert doc.doc_type == DocType.OTHER.value
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value

    # An attorney reclassifies it to a medical record via the API function (clears review, no status
    # reset — it stays OCR_DONE).
    reclassify_document(db, user=dev_user, document=doc, doc_type=DocType.MEDICAL_RECORD)
    db.refresh(doc)
    assert doc.doc_type == DocType.MEDICAL_RECORD.value
    assert doc.status == DocStatus.OCR_DONE.value

    # Re-run: the OCR_DONE doc is picked up extraction-only (no re-classify), the single window
    # runs, the doc reaches EXTRACTED, and a FACT token is minted. Late-documents run -> gate
    # untouched.
    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_encounter(1)]),
            run_logger=logger,
        )
    )
    parsed = _parse_frames(frames)

    db.refresh(doc)
    assert doc.status == DocStatus.EXTRACTED.value
    # Exactly one extraction run for this doc, ok.
    runs = db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).all()
    assert len(runs) == 1

    # No re-classify happened on the rerun (only the first run's classify is on the ledger).
    assert _llm_stages(db, matter).count("phase0.classify") == 1

    # A FACT token for the encounter was minted; gate untouched (late-documents branch).
    encounters = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).all()
    assert len(encounters) == 1
    fact_tokens = (
        db.query(FactToken)
        .filter(FactToken.matter_id == matter.id, FactToken.kind == TokenKind.FACT.value)
        .all()
    )
    assert len(fact_tokens) >= 1
    assert not any(name == SseEvent.GATE_READY.value for name, _ in parsed)
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value
