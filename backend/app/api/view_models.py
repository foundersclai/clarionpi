"""View-models — the only shapes the wire ever sees (04 invariant 11).

Handlers return these, never ORM rows: the view-model is the contract with the frontend, and AI
overlays (none yet at M0) would live only here on responses, never echoed back on a submit. A
:class:`MatterView` is built from an ORM :class:`~app.models.orm.Matter` via
:func:`matter_to_view`, which rehydrates the stored ``sol_candidates`` JSON into typed
:class:`~app.models.schemas.DeadlineCandidate` objects so the deadline banner data is validated,
not raw JSON.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.engine.orchestrator.service import deadlines_all_confirmed
from app.models.enums import (
    ClaimType,
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    TextSource,
    UploadSessionStatus,
)
from app.models.orm import (
    CaseDocument,
    DedupDecision,
    DocumentPage,
    IncidentFacts,
    Matter,
    StrategyInputs,
    UploadSession,
    UploadSlot,
)
from app.models.schemas import DeadlineCandidate


class MatterView(BaseModel):
    """The matter shape returned by the matter endpoints (create + fetch).

    ``deadline_candidates`` are the rules-computed SOL / notice-of-claim deadlines the FE renders
    as a non-dismissible banner until the attorney confirms them at G1 (invariant 4).
    """

    id: uuid.UUID
    client_display_name: str
    claim_type: ClaimType
    jurisdiction: str
    incident_date: date
    gate_state: GateState
    registry_version: int
    deadline_candidates: list[DeadlineCandidate] = Field(default_factory=list)


def matter_to_view(matter: Matter) -> MatterView:
    """Project an ORM :class:`~app.models.orm.Matter` into its wire :class:`MatterView`.

    The persisted ``sol_candidates`` JSON is validated back into
    :class:`~app.models.schemas.DeadlineCandidate` models here, so a malformed stored candidate
    surfaces as a validation error rather than leaking raw JSON to the client.
    """
    candidates = [DeadlineCandidate.model_validate(raw) for raw in matter.sol_candidates]
    return MatterView(
        id=matter.id,
        client_display_name=matter.client_display_name,
        claim_type=ClaimType(matter.claim_type),
        jurisdiction=matter.jurisdiction,
        incident_date=matter.incident_date,
        gate_state=GateState(matter.gate_state),
        registry_version=matter.registry_version,
        deadline_candidates=candidates,
    )


# --------------------------------------------------------------------------------------
# Corpus-ingest view-models (M1)
# --------------------------------------------------------------------------------------


class UploadSlotView(BaseModel):
    """One file slot in an upload session, as the wire sees it.

    ``upload_url`` is where the client PUTs the bytes; the view never computes it — the
    sessions layer decides the URL (presigned, or an app-mediated route) and passes it to
    :func:`upload_slot_to_view`. ``None`` means the slot is not (yet) offering an upload URL.
    """

    id: uuid.UUID
    filename: str
    size_bytes: int
    received: bool
    upload_url: str | None = None


def upload_slot_to_view(slot: UploadSlot, upload_url: str | None) -> UploadSlotView:
    """Project an ORM :class:`~app.models.orm.UploadSlot` plus a caller-supplied URL."""
    return UploadSlotView(
        id=slot.id,
        filename=slot.filename,
        size_bytes=slot.size_bytes,
        received=slot.received,
        upload_url=upload_url,
    )


class UploadSessionView(BaseModel):
    """An upload session and its slots, as the wire sees it."""

    id: uuid.UUID
    matter_id: uuid.UUID
    status: UploadSessionStatus
    ttl_expires_at: datetime
    slots: list[UploadSlotView] = Field(default_factory=list)


def upload_session_to_view(
    session: UploadSession, slots: list[UploadSlotView]
) -> UploadSessionView:
    """Project an ORM :class:`~app.models.orm.UploadSession` with its already-projected slots.

    Slot URLs are decided by the sessions layer, so slot views are built there and passed in.
    """
    return UploadSessionView(
        id=session.id,
        matter_id=session.matter_id,
        status=UploadSessionStatus(session.status),
        ttl_expires_at=session.ttl_expires_at,
        slots=slots,
    )


class DocumentView(BaseModel):
    """A case document as the wire sees it (post-classification)."""

    id: uuid.UUID
    matter_id: uuid.UUID
    doc_type: DocType
    status: DocStatus
    dedup_status: DedupStatus
    filename: str
    page_count: int
    needs_review: bool
    classification_confidence: float | None = None
    failure_reason: str | None = None


def document_to_view(doc: CaseDocument) -> DocumentView:
    """Project an ORM :class:`~app.models.orm.CaseDocument` into its wire view."""
    return DocumentView(
        id=doc.id,
        matter_id=doc.matter_id,
        doc_type=DocType(doc.doc_type),
        status=DocStatus(doc.status),
        dedup_status=DedupStatus(doc.dedup_status),
        filename=doc.filename,
        page_count=doc.page_count,
        needs_review=doc.needs_review,
        classification_confidence=doc.classification_confidence,
        failure_reason=doc.failure_reason,
    )


class PageView(BaseModel):
    """A single document page as the wire sees it. ``ocr_confidence`` is a score, not money."""

    id: uuid.UUID
    document_id: uuid.UUID
    page_no: int
    text: str
    text_source: TextSource
    ocr_confidence: float | None = None
    zero_text: bool
    image_ref: str | None = None


def page_to_view(page: DocumentPage) -> PageView:
    """Project an ORM :class:`~app.models.orm.DocumentPage` into its wire view."""
    return PageView(
        id=page.id,
        document_id=page.document_id,
        page_no=page.page_no,
        text=page.text,
        text_source=TextSource(page.text_source),
        ocr_confidence=page.ocr_confidence,
        zero_text=page.zero_text,
        image_ref=page.image_ref,
    )


class DedupDecisionView(BaseModel):
    """A quarantined dedup decision as the wire sees it. ``shingle_overlap`` is a score."""

    id: uuid.UUID
    matter_id: uuid.UUID
    document_id: uuid.UUID
    against_document_id: uuid.UUID | None = None
    status: DedupStatus
    page_hash_matches: list = Field(default_factory=list)
    shingle_overlap: float | None = None
    resolution: DedupResolution


def dedup_decision_to_view(decision: DedupDecision) -> DedupDecisionView:
    """Project an ORM :class:`~app.models.orm.DedupDecision` into its wire view."""
    return DedupDecisionView(
        id=decision.id,
        matter_id=decision.matter_id,
        document_id=decision.document_id,
        against_document_id=decision.against_document_id,
        status=DedupStatus(decision.status),
        page_hash_matches=decision.page_hash_matches,
        shingle_overlap=decision.shingle_overlap,
        resolution=DedupResolution(decision.resolution),
    )


# --------------------------------------------------------------------------------------
# Gate view-models (M3 Wave B) — per-gate ``view_model`` payloads for the gates envelope.
#
# These build JSON-safe plain dicts (dates already ISO, uuids stringified) rather than
# pydantic models: the envelope is heterogeneous per gate, and the gates route passes the
# whole thing through ``wire_guard.scan_wire_payload`` before it leaves — the scanner walks
# dict/list/str, so the builders emit exactly that.
# --------------------------------------------------------------------------------------


def facts_review_vm(
    matter: Matter,
    incident: IncidentFacts | None,
    documents_summary: dict,
) -> dict:
    """The G1 (facts_review) view-model.

    ``deadline_candidates`` are the stored candidates validated back through
    :class:`~app.models.schemas.DeadlineCandidate` (malformed JSON fails loud, never leaks)
    plus a ``rule_id`` — the candidate's ``statute_cite``, the identifier the FE echoes in
    ``DeadlineConfirmation`` submits. ``incident_facts`` carries payload + anchors, or ``None``
    when no row exists yet. ``documents_summary`` is the route-computed
    ``{"total", "needs_review", "failed"}`` counts.
    """
    candidates: list[dict] = []
    for raw in matter.sol_candidates or []:
        candidate = DeadlineCandidate.model_validate(raw).model_dump(mode="json")
        candidate["rule_id"] = candidate["statute_cite"]
        candidates.append(candidate)
    incident_facts = None
    if incident is not None:
        incident_facts = {
            "payload": dict(incident.payload or {}),
            "anchors": list(incident.anchors or []),
        }
    return {
        "deadline_candidates": candidates,
        "incident_facts": incident_facts,
        "documents_summary": documents_summary,
    }


def strategy_intake_vm(matter: Matter, inputs: StrategyInputs | None) -> dict:
    """The G1.5 (strategy_intake) view-model: current StrategyInputs values (or defaults).

    ``deadlines_confirmed`` is context the FE renders (G1 is behind us — every deadline was
    confirmed to get here); computed by the service's single D1 predicate, not re-derived.
    """
    return {
        "strategy_inputs": {
            "liability_theory": inputs.liability_theory if inputs else "",
            "injury_framing": inputs.injury_framing if inputs else "",
            "emphasis_notes": inputs.emphasis_notes if inputs else "",
            "venue_posture": inputs.venue_posture if inputs else "",
            "anchor_amount_cents": inputs.anchor_amount_cents if inputs else None,
            "mmi_date": (
                inputs.mmi_date.isoformat() if inputs is not None and inputs.mmi_date else None
            ),
            "property_damage_estimate_cents": (
                inputs.property_damage_estimate_cents if inputs else None
            ),
        },
        "deadlines_confirmed": deadlines_all_confirmed(matter),
    }


def minimal_gate_vm(state: GateState) -> dict:
    """The honest placeholder for gates whose UI lands in a later milestone."""
    return {"state": state.value, "detail": "gate UI lands in a later milestone"}
