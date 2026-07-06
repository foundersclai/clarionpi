"""Gate routes — the current-gate envelope + the gate-action submit (M3 Wave B).

Thin by design (api_and_wire §4): tenancy through ``get_tenant_session`` (a cross-firm matter
404s, never 403 — existence must not leak), all gate legality through
:mod:`app.engine.orchestrator.service` (``machine.advance`` + ``guards.evaluate`` under the
hood — this module decides nothing), and every response envelope passed through
:func:`~app.api.wire_guard.scan_wire_payload` before it leaves (invariant 11).

Typed error mapping (the service raises, this module translates):

| service refusal            | HTTP | body ``error``               |
|----------------------------|------|------------------------------|
| GateStateMismatch          | 409  | ``gate_state_mismatch``      |
| StalePayloadVersion        | 409  | ``stale_payload_version``    |
| GuardRefused (role guard)  | 403  | ``role_forbidden``           |
| GuardRefused (any other)   | 409  | ``guard_failed``             |
| OverrideRequired           | 409  | ``override_required``        |
| IllegalGateAction          | 409  | ``illegal_gate_action``      |
| UnknownDeadlineRule        | 422  | ``unknown_deadline_rule``    |
| EditsNotSupported          | 422  | ``edits_not_supported_at_gate`` |
| InvalidEdits               | 422  | ``invalid_edits``            |
| OverrideReasonRequired     | 422  | ``override_reason_required`` |
| InvalidIdempotencyKey      | 422  | ``invalid_idempotency_key``  |
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.view_models import (
    facts_review_vm,
    matter_to_view,
    minimal_gate_vm,
    strategy_intake_vm,
)
from app.api.wire_guard import scan_wire_payload
from app.engine.orchestrator import service
from app.engine.orchestrator.errors import InvalidIdempotencyKey
from app.models.enums import GateState, UserRole
from app.models.orm import CaseDocument, IncidentFacts, Matter, StrategyInputs, User
from app.models.schemas import GateSubmit

router = APIRouter(prefix="/api", tags=["gates"])

# Module-level dependency singletons (ruff B008; evaluated once — see routes/matters.py).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)

# Roles with an edit affordance at the editable gates: paralegals PREP (G1 confirm lists,
# intake facts), attorneys prep + sign. Admins are platform ops, not case prep — no edit
# affordance (and the role guard already denies them approve: sign-off is personal, inv 8).
_EDIT_ROLES = {UserRole.ATTORNEY.value, UserRole.PARALEGAL.value}
_EDITABLE_STATES = {GateState.FACTS_REVIEW, GateState.STRATEGY_INTAKE}


def _matter_not_found(matter_id: uuid.UUID) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
    )


def _documents_summary(session: Session, matter_id: uuid.UUID) -> dict:
    """Route-computed doc counts for the facts VM: total / needs_review / failed."""
    total = session.execute(
        select(func.count()).select_from(CaseDocument).where(CaseDocument.matter_id == matter_id)
    ).scalar_one()
    needs_review = session.execute(
        select(func.count())
        .select_from(CaseDocument)
        .where(CaseDocument.matter_id == matter_id, CaseDocument.needs_review.is_(True))
    ).scalar_one()
    failed = session.execute(
        select(func.count())
        .select_from(CaseDocument)
        .where(CaseDocument.matter_id == matter_id, CaseDocument.status == "failed")
    ).scalar_one()
    return {"total": total, "needs_review": needs_review, "failed": failed}


def _view_model_for(session: Session, matter: Matter) -> dict:
    """Dispatch the per-gate ``view_model`` builder for the matter's current state."""
    state = GateState(matter.gate_state)
    if state is GateState.FACTS_REVIEW:
        incident = session.execute(
            select(IncidentFacts).where(IncidentFacts.matter_id == matter.id)
        ).scalar_one_or_none()
        return facts_review_vm(matter, incident, _documents_summary(session, matter.id))
    if state is GateState.STRATEGY_INTAKE:
        inputs = session.execute(
            select(StrategyInputs).where(StrategyInputs.matter_id == matter.id)
        ).scalar_one_or_none()
        return strategy_intake_vm(matter, inputs)
    return minimal_gate_vm(state)


def _role_affordances(session: Session, matter: Matter, user: User) -> dict:
    """Compute ``{"can_edit", "can_approve", "approve_blockers"}`` for the current actor.

    ``approve_blockers`` dry-runs the approve guards with the CURRENT context (no side
    effects) — a paralegal sees the role reason, an attorney sees exactly what still blocks.
    ``can_approve`` is true iff the state is approvable and the dry run comes back clean.
    """
    state = GateState(matter.gate_state)
    can_edit = state in _EDITABLE_STATES and user.role in _EDIT_ROLES
    blockers = service.dry_run_approve_blockers(session, matter=matter, user=user)
    can_approve = state in service.GATE_EVENT_BY_APPROVE and not blockers
    return {"can_edit": can_edit, "can_approve": can_approve, "approve_blockers": blockers}


@router.get("/matters/{matter_id}/gates/current", response_model=None)
def get_current_gate(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """The current gate envelope: state + payload_version + per-gate VM + role affordances.

    ``payload_version`` is what the FE must echo on the next submit (the optimistic fence).
    The whole envelope passes through the wire token-scanner before leaving (inv 11).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    envelope = {
        "gate": matter.gate_state,
        "payload_version": service.payload_version(session, matter=matter),
        "view_model": _view_model_for(session, matter),
        "role_affordances": _role_affordances(session, matter, user),
    }
    return JSONResponse(status_code=200, content=scan_wire_payload(envelope, where="gates.current"))


def _submit_error_response(exc: Exception) -> JSONResponse | None:
    """Map a typed service refusal to its wire body; ``None`` for non-service errors."""
    if isinstance(exc, service.GateStateMismatch):
        return JSONResponse(
            status_code=409, content={"error": "gate_state_mismatch", "current": exc.current}
        )
    if isinstance(exc, service.StalePayloadVersion):
        return JSONResponse(
            status_code=409,
            content={"error": "stale_payload_version", "fresh_version": exc.fresh_version},
        )
    if isinstance(exc, service.GuardRefused):
        if exc.guard == "role_attorney":
            # Keep the auth-shaped status: a role refusal is a 403 the FE renders inline
            # (no gray-out), same shape as require_role's typed body (invariant 8).
            return JSONResponse(
                status_code=403,
                content={
                    "error": "role_forbidden",
                    "guard": exc.guard,
                    "code": exc.code,
                    "detail": exc.detail,
                },
            )
        return JSONResponse(
            status_code=409,
            content={
                "error": "guard_failed",
                "guard": exc.guard,
                "code": exc.code,
                "detail": exc.detail,
            },
        )
    if isinstance(exc, service.OverrideRequired):
        return JSONResponse(
            status_code=409,
            content={
                "error": "override_required",
                "guard": exc.guard,
                "code": exc.code,
                "detail": exc.detail,
            },
        )
    if isinstance(exc, service.IllegalGateAction):
        return JSONResponse(
            status_code=409,
            content={"error": "illegal_gate_action", "state": exc.state, "action": exc.action},
        )
    if isinstance(exc, service.UnknownDeadlineRule):
        return JSONResponse(
            status_code=422, content={"error": "unknown_deadline_rule", "rule_id": exc.rule_id}
        )
    if isinstance(exc, service.EditsNotSupported):
        return JSONResponse(
            status_code=422, content={"error": "edits_not_supported_at_gate", "gate": exc.gate}
        )
    if isinstance(exc, service.InvalidEdits):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_edits", "gate": exc.gate, "detail": exc.detail},
        )
    if isinstance(exc, service.OverrideReasonRequired):
        return JSONResponse(
            status_code=422, content={"error": "override_reason_required", "detail": str(exc)}
        )
    if isinstance(exc, InvalidIdempotencyKey):
        return JSONResponse(
            status_code=422, content={"error": "invalid_idempotency_key", "detail": exc.reason}
        )
    return None


@router.post("/matters/{matter_id}/gates/{gate}/submit", response_model=None)
def submit_gate_action(
    matter_id: uuid.UUID,
    gate: str,
    body: GateSubmit,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Apply one gate action via the service; map typed refusals per the module table.

    Success returns the action result + the fresh :class:`MatterView` (so the FE re-renders
    without a second fetch) + the GateRecord id — scanned before leaving (inv 11).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    try:
        result = service.apply_gate_action(
            session, matter=matter, user=user, gate=gate, submit=body
        )
    except Exception as exc:
        response = _submit_error_response(exc)
        if response is None:
            raise
        return response

    payload = {
        "result": {
            "transitioned": result.transitioned,
            "from_state": result.from_state,
            "to_state": result.to_state,
            "replayed": result.replayed,
        },
        "matter": matter_to_view(result.matter).model_dump(mode="json"),
        "record_id": str(result.record.id),
    }
    return JSONResponse(status_code=200, content=scan_wire_payload(payload, where="gates.submit"))
