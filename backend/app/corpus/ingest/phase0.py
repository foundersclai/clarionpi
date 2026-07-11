"""Phase 0 — classify -> pages -> dedup -> extract per document, then sync, streamed over SSE.

Re-entrant for late documents and mid-extraction resumes (component corpus_ingest §1; invariant
14 run logs).

The run composes the already-landed per-stage functions (``classify``, ``pages``, ``dedup``,
``extract_document``) over every pending ``CaseDocument`` for a matter, emitting one SSE frame per
lifecycle step and appending a JSON-lines trail to the matter's ingest run log. After the per-doc
loop it runs the **sync stage** — encounter merge, fact-registry sync, and specials-ledger AMT
mint — then the gate step.

Pending selection (re-entrancy): a doc is pending if it is still ``uploaded`` (never processed) OR
already ``ocr_done`` with no completed extraction. The second case covers three resumes without a
re-classify: an M1-ingested matter whose docs were paged before extraction existed, a doc an
attorney reclassified to an extractable type, and a provider-outage that stopped extraction
mid-document. A doc that fully extracted reaches ``extracted`` and drops out of the pending set, so
a re-POST resumes at the first doc that never finished rather than reprocessing the corpus.

Per-doc branching by entry status:

* an ``uploaded`` doc runs the full pipeline (classify -> pages -> dedup -> extract);
* an ``ocr_done`` doc runs the extraction stage ONLY — classify/pages/dedup already ran on a
  prior run and their commits landed, so re-running them would be wasted work.

Gate consequence:

* A completed run in ``corpus_processing`` advances the matter to ``facts_review`` through the
  gate machine (:func:`~app.engine.orchestrator.machine.advance` — the guardless
  ``CORPUS_PROCESSING -> FACTS_REVIEW`` edge is the only sanctioned way ``gate_state`` moves).
* A **late-document** run (matter already past ``corpus_processing``) processes the new documents
  — now including their extraction + a registry re-sync. Its gate consequence depends on the
  current state: at ``evidence_review`` the run fires the ``EVIDENCE_REVIEW -> ANALYSIS_RUNNING``
  rework edge (``advance`` on ``DOCUMENTS_UPLOADED``) so the new facts flow into the demand on the
  attorney's analysis re-run — this is the partial closure of the earlier M2/M3 deferral. At any
  OTHER mid-flow state the run still leaves the gate untouched: invalidating a plan/draft already in
  progress is flow_04's fuller work, deferred by design, not an oversight.

The per-stage functions each commit their own work and never raise for a bad document (a corrupt
PDF is marked ``FAILED`` in place; a bad extractor window is recorded ``FAILED`` and the doc stays
``ocr_done``, resumable). Extraction has two EXPECTED offline conditions — provider down, budget
exhausted — that stop a doc mid-run and leave it resumable without raising. The sync stage (merge,
registry, ledger) uses NO LLM on its critical path, so its failures are all provider-independent.
The run body is still wrapped so that an *unexpected* exception — one the composed stages did not
absorb — ends the stream with a single ERROR frame after logging it, rather than propagating a raw
traceback to the SSE caller. Because per-document work has already committed and the run is
re-entrant, a re-POST resumes cleanly from the first document that never finished.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.sse_utils import format_sse
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.llm_provider import LLMProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_logs import MatterRunLogger
from app.core.storage import ObjectStorage
from app.corpus.extraction import extract_document, merge_encounters
from app.corpus.ingest.classify import classify_document, sample_text_for
from app.corpus.ingest.dedup import run_dedup
from app.corpus.ingest.pages import build_document_pages
from app.corpus.ocr import OcrEngine
from app.engine.orchestrator.machine import advance
from app.engine.tokenizer import registry
from app.models.enums import DedupStatus, DocStatus, DocType, GateEvent, GateState, SseEvent
from app.models.orm import CaseDocument, Matter, MedicalEncounter, User
from app.money.assemble import compute_matter_ledger
from app.money.specials import amounts_for_registry
from app.rules.errors import RulesError
from app.rules.loader import load_pack_for_pin

# Truncate an unexpected error's detail so one runaway repr can't flood the SSE frame.
_ERROR_DETAIL_MAX = 300

# The extractable doc types — mirrors the extractor's `_KIND_BY_DOC_TYPE` keys (its authority) so we
# can emit the "extracting" in-progress frame ONLY for a doc the extractor will actually process; a
# non-extractable type emits no extraction frame (the runner still returns skipped_reason for it).
_EXTRACTABLE_DOC_TYPES = frozenset({DocType.MEDICAL_RECORD, DocType.BILL, DocType.POLICE_REPORT})


@dataclass(frozen=True)
class Phase0Summary:
    """Roll-up counters for one Phase 0 run — the shape of the final ``completed`` STATUS frame.

    ``gate_advanced`` is ``True`` only when this run moved the matter out of
    ``corpus_processing`` into ``facts_review`` (a first run); a late-document run reports
    ``False`` because it deliberately leaves the gate untouched.

    The extraction/sync counters (``documents_extracted`` .. ``registry_version``) are the M2
    additions: how many docs reached ``extracted`` this run, the anchored rows + rejected anchors
    the extractors produced, the encounter-merge groups collapsed, and the registry facts/amounts
    minted at the resulting ``registry_version``.
    """

    documents_processed: int
    pages_created: int
    ocr_fallbacks: int
    zero_text_pages: int
    failed_documents: int
    dedup_quarantined: int
    gate_advanced: bool
    documents_extracted: int
    extraction_rows: int
    anchors_rejected: int
    encounters_merged: int
    facts_minted: int
    amounts_minted: int
    registry_version: int


def _pending_documents(db: Session, matter: Matter) -> list[CaseDocument]:
    """The matter's documents still needing Phase-0 work, ordered ``(created_at, id)``.

    Two statuses are pending:

    * ``uploaded`` — never processed; runs the full pipeline (classify -> pages -> dedup ->
      extract).
    * ``ocr_done`` — paged + deduped already but not yet ``extracted`` (extraction never ran, was
      re-enabled by a reclassify, or stopped mid-run on a provider/budget outage); runs the
      extraction stage only.

    A doc that fully extracted is ``extracted`` and not pending, so a re-POST resumes at the first
    doc that never finished rather than reprocessing the corpus. ``failed`` docs are terminal and
    excluded.
    """
    return list(
        db.scalars(
            select(CaseDocument)
            .where(
                CaseDocument.matter_id == matter.id,
                or_(
                    CaseDocument.status == DocStatus.UPLOADED.value,
                    CaseDocument.status == DocStatus.OCR_DONE.value,
                ),
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

    Processes every pending document (an ``uploaded`` doc through classify -> pages -> dedup ->
    extract; an ``ocr_done`` doc through extract only), then runs the sync stage (merge -> registry
    sync -> ledger AMT mint) and the gate step: a run that started in ``corpus_processing`` advances
    to ``facts_review``; a late-document run leaves the gate where it is. Re-entrant: a re-POST
    resumes at the first unprocessed document and re-syncs.

    An empty pending set is a legal run (a re-POST after completion): it still emits
    started/completed, still runs the sync stage, and still does the gate step. If the matter is
    still ``corpus_processing`` with zero pending docs, that step DOES advance it — a zero-document
    matter reaching ``facts_review`` is the attorney's problem to see (an empty corpus), not
    something this runner silently blocks.
    """
    logger = run_logger if run_logger is not None else MatterRunLogger(matter.id, "ingest")
    settings = get_settings()

    # Rule-pack pin preflight (BUS-02): reject version/fingerprint drift at ENTRY — before
    # any document, registry, or ledger write — and reuse the returned pack at the ledger
    # stage below (never re-loaded mid-run, so a change-then-revert cannot slip through).
    try:
        pack = load_pack_for_pin(
            matter.jurisdiction,
            matter.rule_pack_version,
            matter.rule_pack_fingerprint,
            require_authoritative=False,
        )
    except RulesError as exc:
        logger.log("run_refused", reason=exc.diagnostic_kind)
        yield format_sse(SseEvent.ERROR, {"phase": "phase0", "error": exc.diagnostic_kind})
        return

    # A document id we can name in the error frame if the wrapped body blows up mid-document.
    current_document_id: str | None = None

    documents_processed = 0
    pages_created = 0
    ocr_fallbacks = 0
    zero_text_pages = 0
    failed_documents = 0
    dedup_quarantined = 0
    documents_extracted = 0
    extraction_rows = 0
    anchors_rejected = 0
    encounters_merged = 0
    facts_minted = 0
    amounts_minted = 0
    registry_version = matter.registry_version

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
            # Every model call travels the metered door (invariant 12). One client per document.
            client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
            # Capture the entry status BEFORE any stage mutates it: it decides the branch.
            entry_status = document.status

            if entry_status == DocStatus.UPLOADED.value:
                # ---- Full pipeline: classify -> pages -> dedup -----------------------------------
                yield format_sse(
                    SseEvent.DOC_STATE,
                    {"document_id": str(document.id), "status": "classifying"},
                )
                sample = sample_text_for(
                    storage, document, max_pages=settings.classifier_sample_pages
                )
                classify_outcome = classify_document(
                    db, client, document=document, sample_text=sample
                )
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

                pages_outcome = build_document_pages(
                    db, storage=storage, ocr=ocr, document=document
                )
                logger.log("doc_pages_built", document_id=str(document.id), **asdict(pages_outcome))

                if pages_outcome.failed:
                    # A poison document is marked FAILED by the pages stage; surface it and move on
                    # (no dedup/extraction for a doc with no page store).
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

            # ---- Extraction stage (both branches converge here) ---------------------------------
            # A freshly-paged doc and a resumed OCR_DONE doc both extract now. The runner is
            # idempotent per (document, window, prompt_version) — a re-run skips ok/partial windows.
            # A non-extractable type (incl. a degraded `other`) is skipped VISIBLY, no frames.
            # Only an extractable-typed doc gets the "extracting" in-progress frame — the
            # extractor's `_KIND_BY_DOC_TYPE` is the authority for that set; we mirror its three
            # keys here so a skipped doc emits no extraction frame at all.
            if DocType(document.doc_type) in _EXTRACTABLE_DOC_TYPES:
                yield format_sse(
                    SseEvent.DOC_STATE,
                    {"document_id": str(document.id), "status": "extracting"},
                )
            extract_outcome = extract_document(db, client, document=document)
            if extract_outcome.skipped_reason is not None:
                logger.log(
                    "doc_extraction_skipped",
                    document_id=str(document.id),
                    reason=extract_outcome.skipped_reason,
                )
            else:
                extraction_rows += extract_outcome.rows_emitted
                anchors_rejected += extract_outcome.anchors_rejected
                # The doc reached EXTRACTED iff every window ran ok/partial (runner rule). We read
                # the persisted status the runner just committed rather than re-deriving it.
                db.refresh(document)
                if document.status == DocStatus.EXTRACTED.value:
                    documents_extracted += 1
                    logger.log(
                        "doc_extracted",
                        document_id=str(document.id),
                        rows_emitted=extract_outcome.rows_emitted,
                        anchors_rejected=extract_outcome.anchors_rejected,
                        runs_failed=extract_outcome.runs_failed,
                    )
                    yield format_sse(
                        SseEvent.DOC_STATE,
                        {
                            "document_id": str(document.id),
                            "status": "extracted",
                            "rows_emitted": extract_outcome.rows_emitted,
                            "anchors_rejected": extract_outcome.anchors_rejected,
                            "runs_failed": extract_outcome.runs_failed,
                        },
                    )
                else:
                    # A window failed (provider/budget outage, or two parse failures): the doc
                    # stays OCR_DONE and a re-run resumes it. Mirror the runner's error string.
                    error = "provider_unavailable" if extract_outcome.runs_failed else "incomplete"
                    logger.log(
                        "doc_extraction_incomplete",
                        document_id=str(document.id),
                        runs_failed=extract_outcome.runs_failed,
                        error=error,
                    )
                    yield format_sse(
                        SseEvent.DOC_STATE,
                        {
                            "document_id": str(document.id),
                            "status": "extraction_incomplete",
                            "runs_failed": extract_outcome.runs_failed,
                            "error": error,
                        },
                    )

            documents_processed += 1

        current_document_id = None

        # ---- Sync stage: merge -> registry sync -> ledger AMT mint --------------------------
        # These paths use NO LLM on their critical work (merge's tiebreak is best-effort and skips
        # cleanly without a client), so their failures are provider-independent; an unexpected one
        # falls through to the run_error contract below. One metered client for the merge tiebreak.
        sync_client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)

        encounter_count = db.scalar(
            select(MedicalEncounter.id).where(MedicalEncounter.matter_id == matter.id).limit(1)
        )
        if encounter_count is not None:
            merge_outcome = merge_encounters(db, sync_client, matter=matter)
            encounters_merged = merge_outcome.merged_groups
            logger.log("encounters_merged", **asdict(merge_outcome))
            yield format_sse(
                SseEvent.STATUS,
                {
                    "phase": "phase0",
                    "state": "encounters_merged",
                    "merged_groups": merge_outcome.merged_groups,
                    "tiebreaks_skipped": merge_outcome.tiebreaks_skipped,
                },
            )

        facts_sync = registry.sync_extracted_facts(db, matter=matter)
        facts_minted = facts_sync.minted
        registry_version = facts_sync.version
        logger.log("registry_synced", **asdict(facts_sync))

        # Ledger AMT mint — uses the pack the ENTRY preflight validated against the
        # matter's pin (BUS-02); a drifted pack never reaches this write.
        ledger = compute_matter_ledger(db, matter=matter, pack=pack)
        amounts = amounts_for_registry(ledger)
        amt_sync = registry.mint_amounts(db, matter=matter, amounts=amounts)
        amounts_minted = amt_sync.minted
        registry_version = amt_sync.version
        logger.log(
            "ledger_amounts_minted",
            count=amt_sync.minted,
            line_set_hash=ledger.line_set_hash,
            demand_basis_total_cents=ledger.demand_basis_total_cents,
            basis=ledger.basis,
        )

        yield format_sse(
            SseEvent.STATUS,
            {
                "phase": "phase0",
                "state": "registry_synced",
                "registry_version": registry_version,
                "facts_minted": facts_minted,
                "amounts_minted": amounts_minted,
            },
        )

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
                    "documents_extracted": documents_extracted,
                    "facts_minted": facts_minted,
                    "amounts_minted": amounts_minted,
                    "registry_version": registry_version,
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
        elif matter.gate_state == GateState.EVIDENCE_REVIEW.value:
            # Late documents WHILE reviewing evidence route to an analysis re-run: fire the
            # guardless EVIDENCE_REVIEW -> ANALYSIS_RUNNING rework edge so the new facts flow into
            # the demand when the attorney re-runs analysis. This partially closes the M2/M3
            # boundary note below — only for the evidence_review case; other mid-flow states keep
            # the plain late-docs behavior in the branch above until flow_04's fuller invalidation.
            transition = advance(GateState.EVIDENCE_REVIEW, GateEvent.DOCUMENTS_UPLOADED)
            matter.gate_state = transition.to.value
            record_event(
                db,
                firm_id=matter.firm_id,
                actor_id=user.id,
                event_kind="late_documents_rework",
                payload={
                    "matter_id": str(matter.id),
                    "documents_processed": documents_processed,
                    "documents_extracted": documents_extracted,
                    "registry_version": registry_version,
                    "gate_state": matter.gate_state,
                },
            )
            db.commit()
            logger.log("late_documents_rework", gate_state=matter.gate_state)
            yield format_sse(
                SseEvent.STATUS,
                {
                    "phase": "phase0",
                    "state": "late_documents_rework",
                    "gate_state": matter.gate_state,
                },
            )
        else:
            # Late-document run at any other mid-flow state: process + extract + re-sync the new
            # docs, leave the gate. The fuller invalidation of a plan/draft in progress is flow_04
            # work, deferred; only the evidence_review case routes to an analysis re-run above.
            record_event(
                db,
                firm_id=matter.firm_id,
                actor_id=user.id,
                event_kind="phase0_late_documents_processed",
                payload={
                    "matter_id": str(matter.id),
                    "documents_processed": documents_processed,
                    "documents_extracted": documents_extracted,
                    "registry_version": registry_version,
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
            documents_extracted=documents_extracted,
            extraction_rows=extraction_rows,
            anchors_rejected=anchors_rejected,
            encounters_merged=encounters_merged,
            facts_minted=facts_minted,
            amounts_minted=amounts_minted,
            registry_version=registry_version,
        )
        logger.log("run_completed", **asdict(summary))
        yield format_sse(
            SseEvent.STATUS,
            {"phase": "phase0", "state": "completed", **asdict(summary)},
        )
    except Exception as exc:
        # The composed stages absorb every EXPECTED bad-document condition themselves (classify
        # degrades, pages marks FAILED, dedup never raises, extraction records a FAILED window and
        # resumes). Reaching here means something genuinely unexpected broke. We do NOT re-raise
        # through the stream: per-document commits already landed and the run is re-entrant, so a
        # re-POST resumes at the first unprocessed doc. Emit one ERROR frame and end cleanly.
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
