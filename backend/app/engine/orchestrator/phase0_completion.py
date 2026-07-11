"""The Phase-0 completion handler (BUS-05) — orchestrator-owned gate decisions for ingest.

Injected into :func:`app.corpus.ingest.phase0.run_phase0` by the API composition layer so
``corpus`` never imports ``engine`` for gate work (the old direct ``machine`` import was a
recorded contract breach; the injection removes it). The handler:

1. row-locks + REFRESHES the matter (the same serialization protocol as ``apply_gate_action``
   and ``apply_registry_bump``) and branches on the state that actually serialized — never on
   the ``Matter`` instance that entered the long-running Phase-0 generator;
2. compares the run's final registry version against the DURABLE cursor
   (``Matter.invalidation_applied_registry_version``), not a run-local pre-run value — a crash
   between registry sync (which commits) and this step is recovered by a no-pending-doc retry;
3. owns the CORPUS_READY advance, the evidence-review DOCUMENTS_UPLOADED re-analysis route,
   and the registry-bump invalidation for every other post-corpus state. A NULL (legacy)
   cursor is treated as lagging — never grandfathered (ADR-0012).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.corpus.ingest.phase0 import Phase0Completion
from app.engine.orchestrator import machine
from app.engine.orchestrator.registry_bump import _lock_matter, apply_registry_bump
from app.models.enums import GateEvent, GateState
from app.models.orm import Matter, User


def handle_phase0_completion(
    db: Session,
    *,
    matter: Matter,
    user: User,
    registry_version: int,
    stats: dict,
) -> Phase0Completion:
    """Decide + apply the gate consequence of a completed Phase-0 run. See the module doc."""
    locked = _lock_matter(db, matter.id)
    cursor = locked.invalidation_applied_registry_version
    cursor_lags = cursor is None or cursor < registry_version
    # The evidence-review rework fires on EITHER trigger: new documents processed this run
    # (the pre-existing contract — even a no-new-facts late doc routes to re-analysis) or a
    # lagging cursor (crash recovery: registry synced but the gate step never ran).
    documents_processed = int(stats.get("documents_processed", 0) or 0)

    if locked.gate_state == GateState.CORPUS_PROCESSING.value:
        transition = machine.advance(GateState.CORPUS_PROCESSING, GateEvent.CORPUS_READY)
        locked.gate_state = transition.to.value
        locked.invalidation_applied_registry_version = registry_version
        record_event(
            db,
            firm_id=locked.firm_id,
            actor_id=user.id,
            event_kind="phase0_completed",
            payload={"matter_id": str(locked.id), **stats},
        )
        db.commit()
        return Phase0Completion(state="corpus_ready", gate_ready=transition.to.value, payload={})

    if locked.gate_state == GateState.EVIDENCE_REVIEW.value and (
        cursor_lags or documents_processed > 0
    ):
        transition = machine.advance(GateState.EVIDENCE_REVIEW, GateEvent.DOCUMENTS_UPLOADED)
        locked.gate_state = transition.to.value
        locked.invalidation_applied_registry_version = registry_version
        record_event(
            db,
            firm_id=locked.firm_id,
            actor_id=user.id,
            event_kind="late_documents_rework",
            payload={
                "matter_id": str(locked.id),
                "registry_version": registry_version,
                "gate_state": locked.gate_state,
            },
        )
        db.commit()
        return Phase0Completion(
            state="late_documents_rework",
            gate_ready=None,
            payload={"gate_state": locked.gate_state},
        )

    if cursor_lags:
        outcome = apply_registry_bump(
            db, matter=locked, user=user, to_registry_version=registry_version
        )
        return Phase0Completion(
            state="registry_bumped",
            gate_ready=None,
            payload={
                "effect": outcome.effect.value if outcome.effect is not None else None,
                "from_gate_state": outcome.from_state,
                "to_gate_state": outcome.to_state,
                "from_registry_version": outcome.from_registry_version,
                "to_registry_version": outcome.to_registry_version,
            },
        )

    record_event(
        db,
        firm_id=locked.firm_id,
        actor_id=user.id,
        event_kind="phase0_late_documents_processed",
        payload={
            "matter_id": str(locked.id),
            "registry_version": registry_version,
            "gate_state": locked.gate_state,
        },
    )
    db.commit()
    return Phase0Completion(
        state="late_documents_processed",
        gate_ready=None,
        payload={"gate_state": locked.gate_state},
    )


__all__ = ["handle_phase0_completion"]
