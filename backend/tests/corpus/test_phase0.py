"""Tests for the Phase 0 runner (``app.corpus.ingest.phase0``) — fast, offline, unmarked.

Deterministic and service-free: synthetic PDFs from ``pdf_builders``, the deterministic
:class:`FakeOcr`, and :class:`ScriptedProvider` / :class:`NullProvider` for classification. The
run log is pinned to a per-test tmp dir (``MatterRunLogger(..., logs_dir=tmp_path)``) so no test
writes the process-default log directory.

Coverage: the happy-path frame order + persisted state + audit + run-log lines; that every
yielded frame is a well-formed SSE frame carrying an ``SseEvent`` name; degraded classify still
ingests + advances; a failed doc doesn't kill the run; a dedup quarantine surfaces; re-entrancy
for late documents; a zero-pending re-POST; the HTTP/SSE route path (200 + cross-firm 404); and
the unexpected-error path (ERROR frame, no exception escapes, ``run_error`` logged).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.ingest import get_ocr, get_provider, router
from app.api.routes.uploads import get_object_storage
from app.core.llm_provider import CompletionResult, NullProvider, ScriptedProvider
from app.core.matter_logs import MatterRunLogger
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.corpus.ingest import phase0 as phase0_module
from app.corpus.ingest.phase0 import run_phase0
from app.corpus.ocr import FakeOcr
from app.models.enums import DedupStatus, DocStatus, DocType, GateState, SseEvent
from app.models.orm import AuditEvent, CaseDocument, DedupDecision, DocumentPage, Matter, User

from .pdf_builders import CORRUPT_PDF_BYTES, build_text_pdf

# The valid-classify JSON the model would return; queue one per document for a clean pass.
_CLASSIFY_JSON = '{"doc_type": "medical_record", "confidence": 0.95, "rationale": "r"}'


def _classify_result() -> CompletionResult:
    """One scripted classify reply (a valid, above-floor medical_record verdict)."""
    return CompletionResult(text=_CLASSIFY_JSON, input_tokens=10, output_tokens=5, cost_cents=1)


def _make_doc(
    db: Session,
    user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    pdf_bytes: bytes,
    *,
    filename: str = "record.pdf",
    store: bool = True,
) -> CaseDocument:
    """Store ``pdf_bytes`` and create an UPLOADED, doc_type=other, unique CaseDocument for it.

    Mirrors what ``commit_session`` produces (status ``uploaded`` pre-classification). ``store``
    False leaves the key pointing at a blob that was never written (the missing-blob failure).
    """
    key = f"matters/{matter.id}/{uuid.uuid4()}.pdf"
    if store:
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


def _text_pages(n: int, tag: str = "A") -> list[str]:
    """``n`` distinct text pages, each well past the density floor (so no OCR).

    ``tag`` salts the page text so two documents built with different tags never collide in
    dedup (distinct content), while two documents built with the same tag are exact duplicates.
    """
    return [
        f"Progress note {tag} page {i}: patient reports improvement over prior visit number {i}."
        for i in range(1, n + 1)
    ]


def _parse_frames(frames: list[str]) -> list[tuple[str, dict]]:
    """Parse ``[format_sse(...) , ...]`` strings into ``[(event_name, data_dict), ...]``.

    Asserts each frame's SSE shape (``event: <name>\\ndata: {json}\\n\\n``) and that the event name
    is a real :class:`SseEvent` value — parsed, not regex-guessed.
    """
    valid_events = {e.value for e in SseEvent}
    parsed: list[tuple[str, dict]] = []
    for frame in frames:
        assert frame.endswith("\n\n"), f"frame must end with a blank line: {frame!r}"
        lines = frame[:-2].split("\n")
        assert len(lines) == 2, f"frame must be exactly event+data lines: {frame!r}"
        assert lines[0].startswith("event: "), frame
        assert lines[1].startswith("data: "), frame
        event_name = lines[0][len("event: ") :]
        assert event_name in valid_events, f"{event_name!r} is not an SseEvent value"
        data = json.loads(lines[1][len("data: ") :])
        parsed.append((event_name, data))
    return parsed


def _log_events(logger: MatterRunLogger) -> list[str]:
    """The ``event`` field of every JSON line in the run log, in order."""
    lines = logger.path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["event"] for line in lines if line.strip()]


def _log_records(logger: MatterRunLogger) -> list[dict]:
    """Every parsed JSON record in the run log, in order."""
    lines = logger.path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# --------------------------------------------------------------------------------------
# Happy path (direct call)
# --------------------------------------------------------------------------------------


def test_happy_path_direct_call(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    doc1 = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_text_pages(2, "A")), filename="a.pdf"
    )
    doc2 = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_text_pages(3, "B")), filename="b.pdf"
    )
    provider = ScriptedProvider([_classify_result(), _classify_result()])
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

    # Frame ORDER: started -> (per doc: classifying, classified, ocr_done) x2 -> gate_ready -> done.
    # Both docs are distinct content (tags A/B), so neither dedup-quarantines.
    assert events == [
        SseEvent.STATUS.value,  # started
        SseEvent.DOC_STATE.value,  # doc1 classifying
        SseEvent.DOC_STATE.value,  # doc1 classified
        SseEvent.DOC_STATE.value,  # doc1 ocr_done
        SseEvent.DOC_STATE.value,  # doc2 classifying
        SseEvent.DOC_STATE.value,  # doc2 classified
        SseEvent.DOC_STATE.value,  # doc2 ocr_done
        SseEvent.GATE_READY.value,
        SseEvent.STATUS.value,  # completed
    ]
    assert parsed[0][1] == {
        "phase": "phase0",
        "state": "started",
        "matter_id": str(matter.id),
        "pending_documents": 2,
    }
    assert parsed[-2][1] == {"gate": "facts_review", "matter_id": str(matter.id)}

    # Both docs ended OCR_DONE with pages.
    for doc, expected_pages in ((doc1, 2), (doc2, 3)):
        db.refresh(doc)
        assert doc.status == DocStatus.OCR_DONE.value
        page_count = db.scalar(
            select(func.count()).select_from(DocumentPage).where(DocumentPage.document_id == doc.id)
        )
        assert page_count == expected_pages

    # Matter advanced through the gate machine.
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value

    # The phase0_completed audit event exists.
    kinds = list(db.scalars(select(AuditEvent.event_kind)))
    assert "phase0_completed" in kinds

    # Run-log has the expected lines.
    log_events = _log_events(logger)
    for expected in ("run_started", "doc_classified", "gate_advanced", "run_completed"):
        assert expected in log_events, f"missing {expected!r} in {log_events}"

    # Summary counters in the completed frame match reality (2 docs, 5 pages, no OCR/fail/dedup).
    completed = parsed[-1][1]
    assert completed["state"] == "completed"
    assert completed["documents_processed"] == 2
    assert completed["pages_created"] == 5
    assert completed["ocr_fallbacks"] == 0
    assert completed["zero_text_pages"] == 0
    assert completed["failed_documents"] == 0
    assert completed["dedup_quarantined"] == 0
    assert completed["gate_advanced"] is True


def test_every_frame_is_valid_sse_shape(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    """Every yielded string is a well-formed SSE frame whose event name is an SseEvent value."""
    _make_doc(db, dev_user, matter, storage, build_text_pdf(_text_pages(1)))
    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_classify_result()]),
            run_logger=MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path),
        )
    )
    # _parse_frames itself asserts the shape + event-name membership for every frame.
    parsed = _parse_frames(frames)
    assert len(parsed) == len(frames) > 0


# --------------------------------------------------------------------------------------
# Degraded classify still ingests
# --------------------------------------------------------------------------------------


def test_degraded_classify_still_ingests_and_advances(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # NullProvider raises ProviderNotConfigured -> classify degrades to other + needs_review.
    doc = _make_doc(db, dev_user, matter, storage, build_text_pdf(_text_pages(2)))
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
    assert doc.status == DocStatus.OCR_DONE.value  # pages still built
    assert doc.doc_type == DocType.OTHER.value
    assert doc.needs_review is True

    # The classified frame carried needs_review; the gate still advanced (review queue != stall).
    classified = next(
        d
        for name, d in parsed
        if name == SseEvent.DOC_STATE.value and d.get("status") == "classified"
    )
    assert classified["needs_review"] is True
    assert classified["doc_type"] == DocType.OTHER.value
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value

    # The doc_classified log line records the degrade.
    classified_log = next(r for r in _log_records(logger) if r["event"] == "doc_classified")
    assert classified_log["degraded"] is True
    assert classified_log["degrade_reason"] == "provider_unavailable"


# --------------------------------------------------------------------------------------
# A failed doc doesn't kill the run
# --------------------------------------------------------------------------------------


def test_failed_doc_does_not_kill_run(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # doc1 corrupt bytes (page build fails), doc2 fine.
    doc1 = _make_doc(db, dev_user, matter, storage, CORRUPT_PDF_BYTES, filename="bad.pdf")
    doc2 = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_text_pages(2, "OK")), filename="ok.pdf"
    )
    provider = ScriptedProvider([_classify_result(), _classify_result()])

    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=provider,
            run_logger=MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path),
        )
    )
    parsed = _parse_frames(frames)

    # doc1 FAILED + failed DOC_STATE frame.
    db.refresh(doc1)
    assert doc1.status == DocStatus.FAILED.value
    failed_frame = next(
        d for name, d in parsed if name == SseEvent.DOC_STATE.value and d.get("status") == "failed"
    )
    assert failed_frame["document_id"] == str(doc1.id)
    assert failed_frame["reason"] is not None

    # doc2 OCR_DONE; gate advances; summary counts the one failure.
    db.refresh(doc2)
    assert doc2.status == DocStatus.OCR_DONE.value
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value
    completed = parsed[-1][1]
    assert completed["failed_documents"] == 1
    assert completed["documents_processed"] == 2


# --------------------------------------------------------------------------------------
# Dedup quarantine surfaces
# --------------------------------------------------------------------------------------


def test_dedup_quarantine_surfaces(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # Same 3-page text stored as two documents (same tag) -> the later one is an exact duplicate
    # of the earlier one. "Earlier" is the (created_at, id) order dedup uses; both share a
    # second-resolution created_at on SQLite, so the random uuid4 id is the real tiebreaker and
    # registration order does NOT determine which is flagged. Derive earlier/later from the id.
    pages = _text_pages(3, "DUP")
    doc_a = _make_doc(db, dev_user, matter, storage, build_text_pdf(pages), filename="one.pdf")
    doc_b = _make_doc(db, dev_user, matter, storage, build_text_pdf(pages), filename="two.pdf")
    earlier, later = sorted((doc_a, doc_b), key=lambda d: (d.created_at, d.id))
    provider = ScriptedProvider([_classify_result(), _classify_result()])

    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=provider,
            run_logger=MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path),
        )
    )
    parsed = _parse_frames(frames)

    quarantined = next(
        d
        for name, d in parsed
        if name == SseEvent.DOC_STATE.value and d.get("status") == "dedup_quarantined"
    )
    assert quarantined["document_id"] == str(later.id)
    assert quarantined["dedup_status"] == DedupStatus.DUPLICATE_OF.value
    assert quarantined["against_document_id"] == str(earlier.id)

    # A pending dedup decision was quarantined for the later document.
    decision = db.scalars(select(DedupDecision).where(DedupDecision.document_id == later.id)).one()
    assert decision.resolution == "pending"
    assert parsed[-1][1]["dedup_quarantined"] == 1


# --------------------------------------------------------------------------------------
# Re-entrancy (late documents)
# --------------------------------------------------------------------------------------


def test_reentrancy_late_documents(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    doc1 = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_text_pages(2, "EARLY")), filename="a.pdf"
    )
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    # First run to facts_review.
    list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_classify_result()]),
            run_logger=logger,
        )
    )
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value
    doc1_pages_before = [
        p.id
        for p in db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == doc1.id)
            .order_by(DocumentPage.page_no)
        )
    ]

    # A NEW uploaded doc arrives late (distinct content, so no dedup against the first run).
    doc2 = _make_doc(
        db, dev_user, matter, storage, build_text_pdf(_text_pages(3, "LATE")), filename="late.pdf"
    )

    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_classify_result()]),
            run_logger=logger,
        )
    )
    parsed = _parse_frames(frames)

    # New doc processed; matter STAYS facts_review (gate untouched by a late run).
    db.refresh(doc2)
    assert doc2.status == DocStatus.OCR_DONE.value
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value

    # late_documents_processed STATUS frame + audit event; no GATE_READY this run.
    assert not any(name == SseEvent.GATE_READY.value for name, _ in parsed)
    late_frame = next(
        d
        for name, d in parsed
        if name == SseEvent.STATUS.value and d.get("state") == "late_documents_processed"
    )
    assert late_frame["gate_state"] == GateState.FACTS_REVIEW.value
    kinds = list(db.scalars(select(AuditEvent.event_kind)))
    assert "phase0_late_documents_processed" in kinds

    # First run's pages untouched.
    doc1_pages_after = [
        p.id
        for p in db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == doc1.id)
            .order_by(DocumentPage.page_no)
        )
    ]
    assert doc1_pages_after == doc1_pages_before


def test_zero_pending_repost(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    _make_doc(db, dev_user, matter, storage, build_text_pdf(_text_pages(1)))
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    # First run advances the gate.
    list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_classify_result()]),
            run_logger=logger,
        )
    )
    # Third run with nothing pending: started + late + completed, no crash, no gate move.
    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=NullProvider(),  # never called: no pending docs
            run_logger=logger,
        )
    )
    parsed = _parse_frames(frames)
    events = [name for name, _ in parsed]
    states = [d.get("state") for name, d in parsed if name == SseEvent.STATUS.value]

    # No docs pending, matter already past corpus_processing: started -> late -> completed, all
    # STATUS frames, no per-doc or gate frames, no crash.
    assert events == [SseEvent.STATUS.value] * 3
    assert states == ["started", "late_documents_processed", "completed"]
    assert parsed[0][1]["pending_documents"] == 0
    assert parsed[-1][1]["documents_processed"] == 0
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value


# --------------------------------------------------------------------------------------
# HTTP / SSE route path
# --------------------------------------------------------------------------------------


@pytest.fixture
def client(
    make_client: Callable[[APIRouter], TestClient],
    storage: LocalDiskStorage,
) -> Iterator[TestClient]:
    """A TestClient for the ingest router with storage/OCR/provider overridden for tests.

    The runner's default MatterRunLogger writes under ``settings.matter_logs_dir``, which the
    APP_ENV=test default (pinned by conftest) points at the system tempdir — so the HTTP path
    never writes into the repo tree even without a per-test logs_dir hook.
    """
    c = make_client(router)
    c.app.dependency_overrides[get_object_storage] = lambda: storage
    c.app.dependency_overrides[get_ocr] = FakeOcr
    c.app.dependency_overrides[get_provider] = lambda: ScriptedProvider(
        [_classify_result() for _ in range(8)]
    )
    try:
        yield c
    finally:
        c.app.dependency_overrides.clear()


def test_http_run_streams_completed(
    client: TestClient, db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    _make_doc(db, dev_user, matter, storage, build_text_pdf(_text_pages(2)))

    resp = client.post(f"/api/matters/{matter.id}/ingest/run")
    assert resp.status_code == 200, resp.text

    frames = [f + "\n\n" for f in resp.text.split("\n\n") if f.strip()]
    parsed = _parse_frames(frames)
    assert len(parsed) >= 4
    assert parsed[-1][1]["state"] == "completed"
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value


def test_http_cross_firm_matter_404(client: TestClient, firm_b_matter: Matter) -> None:
    resp = client.post(f"/api/matters/{firm_b_matter.id}/ingest/run")
    assert resp.status_code == 404
    assert resp.json()["error"] == "matter_not_found"


# --------------------------------------------------------------------------------------
# Unexpected-error path
# --------------------------------------------------------------------------------------


def test_unexpected_error_emits_error_frame(
    db: Session,
    dev_user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_doc(db, dev_user, matter, storage, build_text_pdf(_text_pages(2)))
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path)

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("dedup exploded")

    # run_dedup is called by name inside phase0; patch it there.
    monkeypatch.setattr(phase0_module, "run_dedup", _boom)

    # No exception escapes the generator; it ends with an ERROR frame.
    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=dev_user,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([_classify_result()]),
            run_logger=logger,
        )
    )
    parsed = _parse_frames(frames)
    name, data = parsed[-1]
    assert name == SseEvent.ERROR.value
    assert data["phase"] == "phase0"
    assert data["error"] == "RuntimeError"
    assert "dedup exploded" in data["detail"]

    # Gate never advanced (the error happened before the gate step); run_error logged.
    db.refresh(matter)
    assert matter.gate_state == GateState.CORPUS_PROCESSING.value
    assert "run_error" in _log_events(logger)
