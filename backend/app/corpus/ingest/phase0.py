"""Phase 0 — classify -> page pipeline -> dedup per document, streamed over SSE.

Re-entrant for late documents (component corpus_ingest §1; invariant 14 run logs).

The run composes the already-landed per-stage functions (``classify``, ``pages``, ``dedup``)
over every ``CaseDocument`` still in ``uploaded`` for a matter, emitting one SSE frame per
lifecycle step and appending a JSON-lines trail to the matter's ingest run log. It processes
ONLY ``uploaded`` docs, so a re-POST after completion resumes at the first unprocessed document
rather than reprocessing the corpus.

Gate consequence:

* A completed run in ``corpus_processing`` advances the matter to ``facts_review`` through the
  gate machine (:func:`~app.engine.orchestrator.machine.advance` — the guardless
  ``CORPUS_PROCESSING -> FACTS_REVIEW`` edge is the only sanctioned way ``gate_state`` moves).
* A **late-document** run (matter already past ``corpus_processing``) processes the new documents
  but leaves the gate untouched. The gate consequence of late records — re-running analysis so the
  new pages flow into the demand — belongs to the analysis re-run wave (M2/M3); it is recorded
  here as an explicit boundary, not an oversight.

The per-stage functions each commit their own work and never raise for a bad document (a corrupt
PDF is marked ``FAILED`` in place). The run body is still wrapped so that an *unexpected* exception
— one the composed stages did not absorb — ends the stream with a single ERROR frame after logging
it, rather than propagating a raw traceback to the SSE caller. Because per-document work has
already committed and the run is re-entrant, a re-POST resumes cleanly from the first document that
never finished.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sse_utils import format_sse
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.llm_provider import LLMProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_logs import MatterRunLogger
from app.core.storage import ObjectStorage
from app.corpus.ingest.classify import classify_document, sample_text_for
from app.corpus.ingest.dedup import run_dedup
from app.corpus.ingest.pages import build_document_pages
from app.corpus.ocr import OcrEngine
from app.engine.orchestrator.machine import advance
from app.models.enums import DedupStatus, DocStatus, GateEvent, GateState, SseEvent
from app.models.orm import CaseDocument, Matter, User

# Truncate an unexpected error's detail so one runaway repr can't flood the SSE frame.
_ERROR_DETAIL_MAX = 300


@dataclass(frozen=True)
class Phase0Summary:
    """Roll-up counters for one Phase 0 run — the shape of the final ``completed`` STATUS frame.

    ``gate_advanced`` is ``True`` only when this run moved the matter out of
    ``corpus_processing`` into ``facts_review`` (a first run); a late-document run reports
    ``False`` because it deliberately leaves the gate untouched.
    """

    documents_processed: int
    pages_created: int
    ocr_fallbacks: int
    zero_text_pages: int
    failed_documents: int
    dedup_quarantined: int
    gate_advanced: bool


def _pending_documents(db: Session, matter: Matter) -> list[CaseDocument]:
    """The matter's ``uploaded`` documents, ordered ``(created_at, id)`` (deterministic).

    Only ``uploaded`` docs are pending: a doc already classified/ocr_done/failed was handled by
    a prior run, so a re-POST resumes at the first unprocessed document rather than reprocessing.
    """
    return list(
        db.scalars(
            select(CaseDocument)
            .where(
                CaseDocument.matter_id == matter.id,
                CaseDocument.status == DocStatus.UPLOADED.value,
            )
            .order_by(CaseDocument.created_at, CaseDocument.id)
        )
    )


def run_phase0(
    db: Session,
    *,
    matter: Matter,
    user: User,
    storage: ObjectStorage,
    ocr: OcrEngine,
    provider: LLMProvider,
    run_logger: MatterRunLogger | None = None,
) -> Iterator[str]:
    """Run Phase 0 for ``matter``, yielding SSE frames (strings from :func:`format_sse`).

    Processes every ``uploaded`` document (classify -> pages -> dedup), then does the gate step:
    a run that started in ``corpus_processing`` advances to ``facts_review``; a late-document run
    leaves the gate where it is. Re-entrant: a re-POST resumes at the first unprocessed document.

    An empty pending set is a legal run (a re-POST after completion): it still emits
    started/completed and still does the gate step. If the matter is still ``corpus_processing``
    with zero pending docs, that step DOES advance it — a zero-document matter reaching
    ``facts_review`` is the attorney's problem to see (an empty corpus), not something this runner
    silently blocks.
    """
    logger = run_logger if run_logger is not None else MatterRunLogger(matter.id, "ingest")
    settings = get_settings()

    # A document id we can name in the error frame if the wrapped body blows up mid-document.
    current_document_id: str | None = None

    documents_processed = 0
    pages_created = 0
    ocr_fallbacks = 0
    zero_text_pages = 0
    failed_documents = 0
    dedup_quarantined = 0

    try:
        pending = _pending_documents(db, matter)
        logger.log(
            "run_started",
            pending_documents=len(pending),
            gate_state=matter.gate_state,
        )
        yield format_sse(
            SseEvent.STATUS,
            {
                "phase": "phase0",
                "state": "started",
                "matter_id": str(matter.id),
                "pending_documents": len(pending),
            },
        )

        for document in pending:
            current_document_id = str(document.id)
            yield format_sse(
                SseEvent.DOC_STATE,
                {"document_id": str(document.id), "status": "classifying"},
            )

            # Every model call travels the metered door (invariant 12). One client per matter run.
            client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
            sample = sample_text_for(storage, document, max_pages=settings.classifier_sample_pages)
            classify_outcome = classify_document(db, client, document=document, sample_text=sample)
            logger.log(
                "doc_classified",
                document_id=str(document.id),
                doc_type=classify_outcome.doc_type,
                confidence=classify_outcome.confidence,
                degraded=classify_outcome.degraded,
                degrade_reason=classify_outcome.degrade_reason,
                needs_review=classify_outcome.needs_review,
            )
            yield format_sse(
                SseEvent.DOC_STATE,
                {
                    "document_id": str(document.id),
                    "status": "classified",
                    "doc_type": classify_outcome.doc_type,
                    "needs_review": classify_outcome.needs_review,
                },
            )

            pages_outcome = build_document_pages(db, storage=storage, ocr=ocr, document=document)
            logger.log("doc_pages_built", document_id=str(document.id), **asdict(pages_outcome))

            if pages_outcome.failed:
                # A poison document is marked FAILED by the pages stage; surface it and move on
                # (no dedup for a doc with no page store).
                failed_documents += 1
                documents_processed += 1
                yield format_sse(
                    SseEvent.DOC_STATE,
                    {
                        "document_id": str(document.id),
                        "status": "failed",
                        "reason": pages_outcome.failure_reason,
                    },
                )
                continue

            pages_created += pages_outcome.pages_created
            ocr_fallbacks += pages_outcome.ocr_fallbacks
            zero_text_pages += pages_outcome.zero_text_pages
            yield format_sse(
                SseEvent.DOC_STATE,
                {
                    "document_id": str(document.id),
                    "status": "ocr_done",
                    "pages_done": pages_outcome.pages_created,
                },
            )

            dedup_outcome = run_dedup(db, document=document)
            logger.log(
                "doc_dedup",
                document_id=str(document.id),
                status=dedup_outcome.status,
                against_document_id=dedup_outcome.against_document_id,
                undedupable=dedup_outcome.undedupable,
            )
            if dedup_outcome.status != DedupStatus.UNIQUE.value:
                dedup_quarantined += 1
                yield format_sse(
                    SseEvent.DOC_STATE,
                    {
                        "document_id": str(document.id),
                        "status": "dedup_quarantined",
                        "dedup_status": dedup_outcome.status,
                        "against_document_id": (
                            str(dedup_outcome.against_document_id)
                            if dedup_outcome.against_document_id is not None
                            else None
                        ),
                    },
                )

            documents_processed += 1

        current_document_id = None

        # ---- Gate step ---------------------------------------------------------------------
        gate_advanced = False
        if matter.gate_state == GateState.CORPUS_PROCESSING.value:
            # The ONLY sanctioned move: the guardless corpus_processing -> facts_review edge.
            transition = advance(GateState.CORPUS_PROCESSING, GateEvent.CORPUS_READY)
            matter.gate_state = transition.to.value
            record_event(
                db,
                firm_id=matter.firm_id,
                actor_id=user.id,
                event_kind="phase0_completed",
                payload={
                    "matter_id": str(matter.id),
                    "documents_processed": documents_processed,
                    "pages_created": pages_created,
                    "ocr_fallbacks": ocr_fallbacks,
                    "zero_text_pages": zero_text_pages,
                    "failed_documents": failed_documents,
                    "dedup_quarantined": dedup_quarantined,
                },
            )
            db.commit()
            logger.log(
                "gate_advanced",
                **{"from": GateState.CORPUS_PROCESSING.value, "to": transition.to.value},
            )
            yield format_sse(
                SseEvent.GATE_READY,
                {"gate": "facts_review", "matter_id": str(matter.id)},
            )
            gate_advanced = True
        else:
            # Late-document run: process the new docs, leave the gate. The re-run of analysis
            # that folds these pages into the demand is the M2/M3 wave's job, not this runner's.
            record_event(
                db,
                firm_id=matter.firm_id,
                actor_id=user.id,
                event_kind="phase0_late_documents_processed",
                payload={
                    "matter_id": str(matter.id),
                    "documents_processed": documents_processed,
                    "gate_state": matter.gate_state,
                },
            )
            db.commit()
            logger.log("late_documents_processed", gate_state=matter.gate_state)
            yield format_sse(
                SseEvent.STATUS,
                {
                    "phase": "phase0",
                    "state": "late_documents_processed",
                    "gate_state": matter.gate_state,
                },
            )

        summary = Phase0Summary(
            documents_processed=documents_processed,
            pages_created=pages_created,
            ocr_fallbacks=ocr_fallbacks,
            zero_text_pages=zero_text_pages,
            failed_documents=failed_documents,
            dedup_quarantined=dedup_quarantined,
            gate_advanced=gate_advanced,
        )
        logger.log("run_completed", **asdict(summary))
        yield format_sse(
            SseEvent.STATUS,
            {"phase": "phase0", "state": "completed", **asdict(summary)},
        )
    except Exception as exc:
        # The composed stages absorb every EXPECTED bad-document condition themselves (classify
        # degrades, pages marks FAILED, dedup never raises). Reaching here means something
        # genuinely unexpected broke. We do NOT re-raise through the stream: per-document commits
        # already landed and the run is re-entrant, so a re-POST resumes at the first unprocessed
        # doc. Emit one ERROR frame and end the stream cleanly instead of leaking a traceback.
        logger.log(
            "run_error",
            error=type(exc).__name__,
            document_id=current_document_id,
        )
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "phase0",
                "error": type(exc).__name__,
                "detail": str(exc)[:_ERROR_DETAIL_MAX],
            },
        )
        return
