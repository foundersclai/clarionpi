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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.brain1 import chronology as chronology_service
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
    Exhibit,
    IncidentFacts,
    Matter,
    RiskFlag,
    StrategyInputs,
    UploadSession,
    UploadSlot,
)
from app.models.schemas import DeadlineCandidate
from app.models.schemas import RiskFlag as RiskFlagView
from app.money.assemble import compute_matter_ledger
from app.money.specials import SpecialsLedger
from app.package import manifest as manifest_service
from app.package.manifest import DraftBinderManifest
from app.rules.errors import UnsupportedJurisdiction
from app.rules.loader import load_pack


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


# --------------------------------------------------------------------------------------
# G2a (evidence_review) view-model (M4 Wave C) — the payload the workbench reviews.
#
# Read-only projection over the landed Brain-1 surfaces: the derived chronology, the
# specials ledger, the anchored risk flags, and the draft-binder manifest. It NEVER spends
# LLM budget — chronology is rebuilt with ``generate_narratives=False`` (narratives already
# persist from the analysis run; a GET must be free), and no manifest tokens are minted.
# Every value is JSON-safe (dates ISO, uuids stringified, tokens exposed as BARE ids) so the
# gates route's ``wire_guard.scan_wire_payload`` passes it through untouched.
# --------------------------------------------------------------------------------------


def _risk_flag_view(flag: RiskFlag) -> dict:
    """One RiskFlag as the wire sees it — the extended view incl. detector + disposition_role."""
    return RiskFlagView.model_validate(flag).model_dump(mode="json")


def _bare_exhibit_token_id(token: str | None) -> str | None:
    """Strip a full token (``[[EX_1]]``) to its bare id (``EX_1``) — never leak token shape."""
    if token is None:
        return None
    return token.removeprefix("[[").removesuffix("]]")


def _manifest_evidence_view(m: DraftBinderManifest, exhibit_id_by_document: dict) -> dict:
    """Serialize the manifest for the VM — EX tokens exposed as BARE ids (mirrors evidence.py).

    ``exhibit_id_by_document`` maps document_id -> Exhibit row id so the UI can drive the
    PHI endpoint (keyed by exhibit id) straight from the VM, matching evidence.py's manifest
    route serialization.
    """
    return {
        "entries": [
            {
                "exhibit_id": (
                    str(exhibit_id_by_document[e.document_id])
                    if e.document_id in exhibit_id_by_document
                    else None
                ),
                "exhibit_token_id": _bare_exhibit_token_id(e.exhibit_token),
                "document_id": str(e.document_id),
                "filename": e.filename,
                "included_pages": list(e.included_pages),
                "excluded_pages": list(e.excluded_pages),
                "phi_disposition": e.phi_disposition,
                "sort_order": e.sort_order,
                "page_count": e.page_count,
                "integrity": e.integrity,
            }
            for e in m.entries
        ],
        "blocking": list(m.blocking),
    }


def _ledger_evidence_view(ledger: SpecialsLedger) -> dict:
    """Serialize the specials ledger for the VM — integer cents only, the FE renders (inv 10).

    Carries the same columns evidence.py exposes plus the two visibility lists the G2a workbench
    surfaces (``missing_paid_line_ids`` / ``excluded_line_ids`` — a gap is shown, never swallowed).
    """

    def _cols(cols: object) -> dict:
        return {
            "billed_cents": cols.billed_cents,  # type: ignore[attr-defined]
            "adjusted_cents": cols.adjusted_cents,  # type: ignore[attr-defined]
            "paid_cents": cols.paid_cents,  # type: ignore[attr-defined]
            "outstanding_cents": cols.outstanding_cents,  # type: ignore[attr-defined]
        }

    return {
        "grand_total": _cols(ledger.grand_total),
        "by_category": {cat: _cols(cols) for cat, cols in ledger.by_category.items()},
        "demand_basis_total_cents": ledger.demand_basis_total_cents,
        "basis": ledger.basis,
        "line_set_hash": ledger.line_set_hash,
        "missing_paid_line_ids": list(ledger.missing_paid_line_ids),
        "excluded_line_ids": list(ledger.excluded_line_ids),
    }


def evidence_review_vm(db: Session, matter: Matter) -> dict:
    """The G2a (evidence_review) view-model: chronology + ledger + risk flags + exhibits.

    Composed from the landed Brain-1 read surfaces, budget-free:

    * ``chronology`` — ``build_chronology(..., generate_narratives=False)`` then
      ``render_rows_for_wire`` (tokens resolved to display forms; NOTHING token-shaped survives).
      ``conflicts`` / ``parked`` are the overlay-quarantine counts. Narratives already persist from
      the analysis run — regenerating on a GET would spend LLM budget, so it is disabled here.
    * ``ledger`` — the specials ledger (cents ints). ``None`` if the jurisdiction pack is
      unsupported (defensive; matter creation already gates it).
    * ``risk_flags`` — every RiskFlag for the matter (incl. detector + disposition_role + anchors),
      ordered ``(severity-desc-ish by created_at, id)`` deterministically.
    * ``exhibits`` — the draft-binder manifest entries (bare ``exhibit_token_id``) + ``blocking``,
      built WITHOUT minting tokens.
    * ``dedup_pending`` — count of still-unresolved dedup decisions (a G2a prep visibility count).
    """
    outcome = chronology_service.build_chronology(
        db, None, matter=matter, generate_narratives=False
    )
    chronology = {
        "rows": chronology_service.render_rows_for_wire(db, matter=matter, rows=outcome.rows),
        "conflicts": outcome.overlays_conflict,
        "parked": outcome.overlays_parked,
    }

    ledger_view: dict | None = None
    try:
        pack = load_pack(matter.jurisdiction)
    except UnsupportedJurisdiction:
        ledger_view = None
    else:
        ledger_view = _ledger_evidence_view(compute_matter_ledger(db, matter=matter, pack=pack))

    flags = list(
        db.execute(
            select(RiskFlag)
            .where(RiskFlag.matter_id == matter.id)
            .order_by(RiskFlag.created_at, RiskFlag.id)
        ).scalars()
    )
    risk_flags = [_risk_flag_view(f) for f in flags]

    manifest = manifest_service.build_draft_manifest(db, matter=matter, mint_tokens=False)
    exhibit_id_by_document = {
        doc_id: ex_id
        for ex_id, doc_id in db.execute(
            select(Exhibit.id, Exhibit.document_id).where(Exhibit.matter_id == matter.id)
        )
    }

    dedup_pending = db.execute(
        select(func.count())
        .select_from(DedupDecision)
        .where(
            DedupDecision.matter_id == matter.id,
            DedupDecision.resolution == DedupResolution.PENDING.value,
        )
    ).scalar_one()

    return {
        "chronology": chronology,
        "ledger": ledger_view,
        "risk_flags": risk_flags,
        "exhibits": _manifest_evidence_view(manifest, exhibit_id_by_document),
        "dedup_pending": dedup_pending,
    }
