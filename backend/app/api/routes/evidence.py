"""Evidence-workbench routes (M4) — exhibit picks, PHI disposition, manifest, ledger read/edits,
chronology overlays.

Thin by design (api_and_wire §4): tenancy through ``get_tenant_session`` (a cross-firm id 404s,
never 403 — existence must not leak), all domain logic in the owning services
(:mod:`app.package.manifest` for picks/manifest, :mod:`app.money.edits` for the source-row ledger
edits, :mod:`app.engine.brain1.chronology` for overlays), and every response passed through
:func:`~app.api.wire_guard.scan_wire_payload` before it leaves (invariant 11).

Gate-state fence: picks, billing edits, and chronology overlays are the paralegal's evidence-prep
work, so they are refused unless the matter is at ``evidence_review`` — a ``409
gate_state_mismatch`` with the current state (the same shape the gate submit uses). Read-only
endpoints (the manifest and the billing-lines list) are allowed at any state (safe read-models).

Wire rule for tokens: the manifest carries EX tokens, but a token-shaped string (``[[EX_1]]``) must
never leave on a wire. The serialization exposes the BARE id (``exhibit_token_id: "EX_1"``) instead
— the internal dataclass keeps the bracketed form; the route strips to the bare id, so the wire
scanner passes.

Typed error mapping:

| service refusal                 | HTTP | body ``error``          |
|---------------------------------|------|-------------------------|
| gate not evidence_review        | 409  | ``gate_state_mismatch`` |
| InvalidPick                     | 422  | ``invalid_pick``        |
| PhiDispositionForbidden         | 403  | ``role_forbidden``      |
| UnknownBillingLine              | 422  | ``unknown_billing_line``|
| MoneyParseError                 | 422  | ``invalid_money_string``|
| encounter not on matter         | 404  | ``encounter_not_found`` |
| overlay edit vocab (schema 422) | 422  | ``invalid_edits``       |
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.wire_guard import scan_wire_payload
from app.engine.brain1 import chronology as chronology_service
from app.models.enums import GateState, PhiDisposition
from app.models.orm import (
    BillingLine,
    ChronologyRowOverlay,
    Exhibit,
    Matter,
    MedicalEncounter,
    User,
)
from app.models.schemas import (
    BillingLineEditBatch,
    ChronologyOverlayRequest,
    ExhibitPickRequest,
)
from app.money.edits import UnknownBillingLine, apply_billing_edits
from app.money.specials import SpecialsLedger
from app.money.types import MoneyParseError
from app.package import manifest as manifest_service
from app.rules.errors import RulesError
from app.rules.loader import load_pack_for_pin

router = APIRouter(prefix="/api", tags=["evidence"])

# Module-level dependency singletons (ruff B008; evaluated once — see routes/matters.py).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)
# The overlay body is taken as a RAW dict (validated inside the handler for the typed
# `invalid_edits` refusal); ``embed=False`` so the request body IS the object, not a wrapper.
_OverlayBody = Body(embed=False)


class PhiDispositionBody(BaseModel):
    """The PHI-disposition POST body — a closed local model (only cleared/excluded via this path).

    ``pending`` is not a valid target here: an attorney dispositions a flag TO a decision; setting
    it back to pending is not an action this endpoint offers.
    """

    model_config = ConfigDict(extra="forbid")

    disposition: PhiDisposition


def _matter_not_found(matter_id: uuid.UUID) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
    )


def _gate_state_mismatch(matter: Matter) -> JSONResponse:
    """Evidence-prep fence: picks/edits require ``evidence_review`` (same shape as gate submit)."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": "gate_state_mismatch", "current": matter.gate_state},
    )


def _exhibit_view(exhibit: Exhibit) -> dict:
    """The inline exhibit view returned by the pick + PHI endpoints (no tokens, plain scalars)."""
    return {
        "id": str(exhibit.id),
        "document_id": str(exhibit.document_id),
        "include_pages": list(exhibit.include_pages),
        "excluded_pages": list(exhibit.excluded_pages),
        "phi_disposition": exhibit.phi_disposition,
        "sort_order": exhibit.sort_order,
    }


def _bare_token_id(token: str | None) -> str | None:
    """Strip a full token (``[[EX_1]]``) to its bare id (``EX_1``) — never leak token shape."""
    if token is None:
        return None
    return token.removeprefix("[[").removesuffix("]]")


def _exhibit_id_by_document(session: Session, matter: Matter) -> dict[str, str]:
    """Map ``document_id -> exhibit_id`` for the matter's exhibits (1:1 per the unique constraint).

    The manifest read-model (``ManifestEntry``) keys entries by ``document_id``, but
    ``POST /api/exhibits/{exhibit_id}/phi`` is keyed by the Exhibit row id — so the wire
    serialization surfaces ``exhibit_id`` too, letting the workbench drive PHI actions straight from
    the manifest view. Resolved here (not on the read-model dataclass) so the package read-model
    stays document-keyed.
    """
    rows = session.execute(
        select(Exhibit.id, Exhibit.document_id).where(Exhibit.matter_id == matter.id)
    ).all()
    return {str(document_id): str(exhibit_id) for exhibit_id, document_id in rows}


def _manifest_view(m: manifest_service.DraftBinderManifest, exhibit_ids: dict[str, str]) -> dict:
    """Serialize the manifest for the wire — EX tokens exposed as BARE ids, never token-shaped.

    ``exhibit_ids`` maps ``document_id -> exhibit_id`` (see :func:`_exhibit_id_by_document`) so each
    entry carries the Exhibit row id the PHI endpoint is keyed by.
    """
    return {
        "matter_id": str(m.matter_id),
        "entries": [
            {
                "exhibit_id": exhibit_ids.get(str(e.document_id)),
                "exhibit_token_id": _bare_token_id(e.exhibit_token),
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


def _ledger_view(ledger: SpecialsLedger) -> dict:
    """Serialize the specials ledger for the wire — integer cents only, the FE renders."""

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
    }


@router.put("/matters/{matter_id}/exhibits", response_model=None)
def put_exhibit_pick(
    matter_id: uuid.UUID,
    body: ExhibitPickRequest,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Upsert a per-document exhibit pick; 409 outside evidence_review, 422 on an invalid pick."""
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    if matter.gate_state != GateState.EVIDENCE_REVIEW.value:
        return _gate_state_mismatch(matter)
    try:
        exhibit = manifest_service.upsert_exhibit_pick(session, user=user, matter=matter, pick=body)
    except manifest_service.InvalidPick as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_pick", "reason": exc.reason, "detail": exc.detail},
        )
    return JSONResponse(
        status_code=200, content=scan_wire_payload(_exhibit_view(exhibit), where="evidence.pick")
    )


@router.post("/exhibits/{exhibit_id}/phi", response_model=None)
def post_phi_disposition(
    exhibit_id: uuid.UUID,
    body: PhiDispositionBody,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Set an exhibit's PHI disposition (attorney-only → typed 403 otherwise)."""
    exhibit = session.get(Exhibit, exhibit_id)
    if exhibit is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "exhibit_not_found", "detail": f"no exhibit {exhibit_id}"},
        )
    try:
        updated = manifest_service.set_phi_disposition(
            session, user=user, exhibit=exhibit, disposition=body.disposition
        )
    except manifest_service.PhiDispositionForbidden as exc:
        return JSONResponse(
            status_code=403,
            content={
                "error": "role_forbidden",
                "required": ["attorney"],
                "actual": exc.actual_role,
            },
        )
    return JSONResponse(
        status_code=200, content=scan_wire_payload(_exhibit_view(updated), where="evidence.phi")
    )


@router.get("/matters/{matter_id}/manifest", response_model=None)
def get_manifest(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Return the draft binder manifest — READ-ONLY at every gate (BUS-05).

    The old ``?mint=true`` write-on-GET is gone: exhibit tokens settle ONLY inside the G2a
    confirm side effect, so no GET can bump the registry after plan/draft/package approval
    outside the locked settlement or Phase-0 invalidation. The response exposes each EX
    token as a bare id (``exhibit_token_id``), never a token-shaped string (inv 11).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    m = manifest_service.build_draft_manifest(session, matter=matter, mint_tokens=False)
    exhibit_ids = _exhibit_id_by_document(session, matter)
    return JSONResponse(
        status_code=200,
        content=scan_wire_payload(_manifest_view(m, exhibit_ids), where="evidence.manifest"),
    )


@router.post("/matters/{matter_id}/billing/edits", response_model=None)
def post_billing_edits(
    matter_id: uuid.UUID,
    body: BillingLineEditBatch,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Apply source-row ledger edits; 409 outside evidence_review, 422 on parse / unknown line.

    Returns the edit counts + the recomputed derived ledger (cents ints only) so the grid
    re-renders off one round-trip — the FE never computes a total (inv 10).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    if matter.gate_state != GateState.EVIDENCE_REVIEW.value:
        return _gate_state_mismatch(matter)
    try:
        # Pin door (BUS-02): a drifted/unpinned-mismatched pack refuses BEFORE any edit write.
        pack = load_pack_for_pin(
            matter.jurisdiction,
            matter.rule_pack_version,
            matter.rule_pack_fingerprint,
            require_authoritative=False,
        )
    except RulesError as exc:
        return JSONResponse(status_code=409, content={"error": exc.diagnostic_kind})
    try:
        outcome = apply_billing_edits(session, matter=matter, pack=pack, batch=body)
    except UnknownBillingLine as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "unknown_billing_line", "line_id": str(exc.line_id)},
        )
    except MoneyParseError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_money_string", "detail": str(exc)},
        )
    payload = {
        "outcome": {
            "edited": outcome.edited,
            "recategorized": outcome.recategorized,
            "reparsed_money_fields": outcome.reparsed_money_fields,
        },
        "ledger": _ledger_view(outcome.ledger),
    }
    return JSONResponse(
        status_code=200, content=scan_wire_payload(payload, where="evidence.billing_edits")
    )


def _line_document_id(line: BillingLine) -> str | None:
    """The source document id parsed from a billing line's anchor, or ``None`` if unparseable.

    Display-only asymmetry vs :func:`app.money.assemble._document_id_from_anchor`: THAT parse is
    strict — a malformed anchor is a fatal ``MalformedAnchor`` because it decides money inclusion
    (which document to exclude on dedup). HERE the id is a display convenience (the FE links a grid
    row to its source doc), so a malformed anchor degrades to ``None`` rather than 500-ing the whole
    lines read — a bad anchor is still surfaced (the ledger endpoints raise on it) without blocking
    the grid from rendering the other lines.
    """
    anchor = line.anchor
    if not isinstance(anchor, dict):
        return None
    raw = anchor.get("document_id")
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return str(raw)
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        return None


def _billing_line_view(line: BillingLine) -> dict:
    """One source billing-line row as the grid sees it — integer cents only, the FE renders."""
    return {
        "id": str(line.id),
        "provider": line.provider,
        "date_of_service": line.date_of_service.isoformat(),
        "code": line.code,
        "billed_cents": line.billed_cents,
        "adjusted_cents": line.adjusted_cents,
        "paid_cents": line.paid_cents,
        "outstanding_cents": line.outstanding_cents,
        "category": line.category,
        "document_id": _line_document_id(line),
    }


@router.get("/matters/{matter_id}/billing/lines", response_model=None)
def get_billing_lines(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """List the matter's SOURCE billing-line rows for the G2a ledger grid, ordered for display.

    A read (no gate fence): the grid is previewable at any state. Rows are ordered
    ``(date_of_service, id)`` — a stable, deterministic order. ``document_id`` is parsed from each
    line's anchor JSON (``None`` if malformed — a display concern here, NOT the fatal
    ``MalformedAnchor`` the money layer raises for inclusion; see :func:`_line_document_id`).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    lines = list(
        session.execute(
            select(BillingLine)
            .where(BillingLine.matter_id == matter.id)
            .order_by(BillingLine.date_of_service, BillingLine.id)
        ).scalars()
    )
    payload = {"lines": [_billing_line_view(line) for line in lines]}
    return JSONResponse(
        status_code=200, content=scan_wire_payload(payload, where="evidence.billing_lines")
    )


def _overlay_view(overlay: ChronologyRowOverlay) -> dict:
    """The chronology-overlay row as the wire sees it (plain scalars; no tokens)."""
    return {
        "encounter_id": str(overlay.encounter_id),
        "edited_fields": dict(overlay.edited_fields),
        "status": overlay.status,
        "base_hash_at_edit": overlay.base_hash_at_edit,
    }


@router.put("/matters/{matter_id}/chronology/{encounter_id}/overlay", response_model=None)
def put_chronology_overlay(
    matter_id: uuid.UUID,
    encounter_id: uuid.UUID,
    body: dict = _OverlayBody,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Upsert a paralegal's chronology-row overlay; 409 outside evidence_review, 404 bad encounter.

    Gate-fenced to ``evidence_review`` (the same prep fence as picks/edits). The body is taken as a
    raw dict and validated INSIDE the handler into
    :class:`~app.models.schemas.ChronologyOverlayRequest`, so a closed-vocabulary miss (unknown key,
    non-string value, or empty dict) returns the typed ``422 invalid_edits`` the workbench branches
    on — not FastAPI's default request-validation envelope (this mirrors the gates route's
    ``InvalidEdits`` mapping). The encounter must belong to the matter (else
    ``404 encounter_not_found`` — an id from another matter must not be editable here). The overlay
    upsert audits itself (:func:`app.engine.brain1.chronology.upsert_overlay`).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    if matter.gate_state != GateState.EVIDENCE_REVIEW.value:
        return _gate_state_mismatch(matter)

    try:
        request = ChronologyOverlayRequest.model_validate(body)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_edits", "detail": str(exc)},
        )

    encounter = session.execute(
        select(MedicalEncounter).where(
            MedicalEncounter.id == encounter_id,
            MedicalEncounter.matter_id == matter.id,
        )
    ).scalar_one_or_none()
    if encounter is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "encounter_not_found", "detail": f"no encounter {encounter_id}"},
        )

    overlay = chronology_service.upsert_overlay(
        session,
        user=user,
        matter=matter,
        encounter=encounter,
        edited_fields=request.edited_fields,
    )
    return JSONResponse(
        status_code=200,
        content=scan_wire_payload({"overlay": _overlay_view(overlay)}, where="evidence.overlay"),
    )
