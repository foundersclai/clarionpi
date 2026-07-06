"""Analysis routes (M4 Wave C) — run the Brain-1 analysis (SSE) + disposition a risk flag.

Thin by design (api_and_wire §4): resolve the matter on the tenant-scoped session, then hand a
:class:`~fastapi.responses.StreamingResponse` the :func:`~app.engine.brain1.analysis.run_analysis`
generator. All the build work — registry sync, chronology, ledger AMT mint, risk flags, the gate
step, the run log — lives in the runner; this module only wires the provider and streams frames.
Gate legality moves only through the machine (in the runner, and — for the re-run edge — here via
``machine.advance``); nothing in this module decides a transition on its own.

Two endpoints:

* ``POST /api/matters/{id}/analysis/run`` — kicks the analysis. At ``analysis_running`` it streams
  the fresh build. At ``evidence_review`` it is the FE's "Re-run analysis" button: it fires the
  guardless ``EVIDENCE_REVIEW -> ANALYSIS_RUNNING`` re-run edge, audits it, then streams the same
  runner — one round trip. Any other state is a ``409 gate_state_mismatch``. Any authenticated firm
  member may trigger it: the analysis is a *derived* computation over already-approved inputs, not a
  human gate act (the G1.5 approval that authorized it already happened).
* ``PUT /api/flags/{flag_id}/disposition`` — records a G2a disposition on one risk flag, mapping the
  engine's high-severity role refusal to a typed 403 (invariant 8).

Every non-streaming response passes through :func:`~app.api.wire_guard.scan_wire_payload`.

Typed error mapping:

| condition                          | HTTP | body ``error``          |
|------------------------------------|------|-------------------------|
| matter not in firm scope           | 404  | ``matter_not_found``    |
| run at a non-analysis gate         | 409  | ``gate_state_mismatch`` |
| flag not in firm scope             | 404  | ``flag_not_found``      |
| HighSeverityDispositionForbidden   | 403  | ``role_forbidden``      |
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.routes.ingest import get_provider
from app.api.wire_guard import scan_wire_payload
from app.core.audit import record_event
from app.core.llm_provider import LLMProvider
from app.engine.brain1.analysis import run_analysis
from app.engine.brain1.risk import HighSeverityDispositionForbidden, disposition_flag
from app.engine.orchestrator.machine import advance
from app.models.enums import GateEvent, GateState
from app.models.orm import Matter, RiskFlag, User
from app.models.schemas import FlagDispositionRequest
from app.models.schemas import RiskFlag as RiskFlagView

router = APIRouter(prefix="/api", tags=["analysis"])

# Module-level dependency singletons (FastAPI pattern; avoids a Depends() call in a default
# argument, which ruff B008 flags — the call is evaluated once here, not per signature).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)
_Provider = Depends(get_provider)


def _matter_not_found(matter_id: uuid.UUID) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
    )


def _mark_rerun(session: Session, *, matter: Matter, user: User) -> None:
    """Fire the guardless ``EVIDENCE_REVIEW -> ANALYSIS_RUNNING`` re-run edge + audit it.

    The FE's "Re-run analysis" button lands the matter back in ``analysis_running`` so the runner's
    gate step advances it to ``evidence_review`` again on completion. Committed here so the runner
    (which streams after this returns) sees the moved state; the analysis itself is idempotent, so
    re-running is safe.
    """
    transition = advance(GateState.EVIDENCE_REVIEW, GateEvent.PICKS_CHANGED)
    matter.gate_state = transition.to.value
    record_event(
        session,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind="analysis_rerun_requested",
        payload={"matter_id": str(matter.id), "from_gate": GateState.EVIDENCE_REVIEW.value},
    )
    session.commit()


@router.post("/matters/{matter_id}/analysis/run", response_model=None)
def run_matter_analysis(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    provider: LLMProvider = _Provider,
) -> StreamingResponse | JSONResponse:
    """Run the Brain-1 analysis for ``matter_id`` and stream its SSE frames.

    A matter outside firm scope → ``404`` (never ``403``: an id must not leak cross-tenant). At
    ``analysis_running`` the body is the analysis SSE stream. At ``evidence_review`` this is the
    re-run button: fire the re-run edge, then stream. Any other state → ``409``. FastAPI holds the
    tenant session open until the stream ends.
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)

    if matter.gate_state == GateState.EVIDENCE_REVIEW.value:
        # Re-run semantics: back-edge to analysis_running (audited), then stream the same runner.
        _mark_rerun(session, matter=matter, user=user)
    elif matter.gate_state != GateState.ANALYSIS_RUNNING.value:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "gate_state_mismatch", "current": matter.gate_state},
        )

    return StreamingResponse(
        run_analysis(session, matter=matter, user=user, provider=provider),
        media_type="text/event-stream",
    )


@router.put("/flags/{flag_id}/disposition", response_model=None)
def put_flag_disposition(
    flag_id: uuid.UUID,
    body: FlagDispositionRequest,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Record a G2a disposition on one risk flag; 403 (typed) on a non-attorney HIGH disposition.

    A flag outside firm scope → ``404 flag_not_found``. A paralegal dispositioning a HIGH flag →
    ``403 role_forbidden`` (invariant 8; the engine raises, this maps it). Success returns the
    RiskFlag view (incl. detector + disposition_role), scanned before leaving (inv 11).
    """
    flag = session.get(RiskFlag, flag_id)
    if flag is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "flag_not_found", "detail": f"no flag {flag_id}"},
        )
    try:
        updated = disposition_flag(session, user=user, flag=flag, request=body)
    except HighSeverityDispositionForbidden as exc:
        return JSONResponse(
            status_code=403,
            content={
                "error": "role_forbidden",
                "required": [exc.required_role],
                "actual": exc.actual,
            },
        )
    view = RiskFlagView.model_validate(updated).model_dump(mode="json")
    return JSONResponse(
        status_code=200, content=scan_wire_payload(view, where="analysis.flag_disposition")
    )
