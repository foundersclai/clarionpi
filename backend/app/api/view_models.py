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
    Matter,
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
