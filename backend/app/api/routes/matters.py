"""Matter routes — create + fetch, the M0 API vertical slice (04 §3).

Thin by design (api_and_wire §4): validate the request, do tenancy/rules work through the owning
modules, return a view-model. Business rules live in ``rules``/``core``, not here.

* ``POST /api/matters`` — load the jurisdiction's rule pack (non-AZ → typed ``422``, per flow_01
  §6: non-AZ creation is refused, typed), compute deadline candidates, create the matter in
  ``corpus_processing``, write a ``matter_created`` audit event, return ``201`` + ``MatterView``.
* ``GET /api/matters/{matter_id}`` — ``200`` + ``MatterView`` in tenant scope, else ``404``
  (never ``403`` — a cross-firm id must not leak the row's existence).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.view_models import MatterView, matter_to_view
from app.core.audit import record_event
from app.core.tenancy import tenant_add
from app.models.enums import GateState
from app.models.orm import Matter, User
from app.models.schemas import MatterCreate
from app.rules.deadlines import compute_deadline_candidates
from app.rules.errors import UnsupportedJurisdiction
from app.rules.loader import load_pack

router = APIRouter(prefix="/api/matters", tags=["matters"])

# The jurisdictions v1 supports — surfaced in the typed refusal body so the FE can render it.
_SUPPORTED_JURISDICTIONS = ["AZ"]

# Module-level dependency singletons (FastAPI pattern; avoids a Depends() call in a default
# argument, which ruff B008 flags — the call is evaluated once here, not per signature).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=None)
def create_matter(
    body: MatterCreate,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> MatterView | JSONResponse:
    """Create a matter and return it with its rules-computed deadline candidates.

    A non-AZ jurisdiction is refused with a typed ``422`` body (``jurisdiction_unsupported``)
    rather than a silent fallback — the rules layer owns the "supported jurisdiction" decision.
    """
    try:
        pack = load_pack(body.jurisdiction)
    except UnsupportedJurisdiction as exc:
        # 422 by integer, not the constant: the constant name is deprecated in this Starlette,
        # and non-AZ is a typed unprocessable-content refusal (flow_01 §6), not a 400.
        return JSONResponse(
            status_code=422,
            content={
                "error": exc.diagnostic_kind,
                "detail": str(exc),
                "supported": _SUPPORTED_JURISDICTIONS,
            },
        )

    candidates = compute_deadline_candidates(pack, body.claim_type, body.incident_date)
    matter = Matter(
        client_display_name=body.client_display_name,
        claim_type=body.claim_type.value,
        incident_date=body.incident_date,
        jurisdiction=body.jurisdiction,
        venue_county=body.venue_county,
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[c.model_dump(mode="json") for c in candidates],
    )
    tenant_add(session, matter, user.firm_id)
    session.flush()  # assign matter.id before it goes into the audit payload

    record_event(
        session,
        firm_id=user.firm_id,
        actor_id=user.id,
        event_kind="matter_created",
        payload={"matter_id": str(matter.id), "jurisdiction": body.jurisdiction},
    )
    session.commit()
    return matter_to_view(matter)


@router.get("", response_model=None)
def list_matters(
    session: Session = _TenantSession,
) -> dict:
    """List the caller's firm's matters, newest first (M3: the FE matter list).

    Tenant-scoped by construction (the session only sees the caller's firm), ordered
    ``created_at`` desc with ``id`` as a stable tiebreak (SQLite timestamps are
    second-resolution), capped at 100 — pagination lands when a captive firm nears the cap.
    """
    matters = session.query(Matter).order_by(Matter.created_at.desc(), Matter.id).limit(100).all()
    return {"matters": [matter_to_view(m) for m in matters]}


@router.get("/{matter_id}", response_model=MatterView)
def get_matter(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
) -> MatterView | JSONResponse:
    """Return a matter view, or ``404`` if it is not in the caller's firm scope.

    The session is firm-scoped, so another firm's matter simply isn't found — the handler returns
    ``404`` (not ``403``), so the endpoint never leaks that a matter exists in another tenant.
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
        )
    return matter_to_view(matter)
