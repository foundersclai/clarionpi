"""Orchestrator gate machine — pure core (data + total functions).

This wave is deliberately persistence-free: the machine is the transition table
(``machine``), the named guards (``guards``), flow_04's invalidation matrix
(``invalidation``), and idempotency-key derivation (``idempotency``). DB, FastAPI, and run
coordination (orchestrator_gates §4) wire in at M1 against this surface.
"""

from __future__ import annotations

from app.engine.orchestrator.errors import IllegalTransition, InvalidIdempotencyKey
from app.engine.orchestrator.guards import (
    REGISTRY as GUARD_REGISTRY,
)
from app.engine.orchestrator.guards import (
    GuardContext,
    GuardResult,
    evaluate,
)
from app.engine.orchestrator.idempotency import derive_key, validate_client_key
from app.engine.orchestrator.invalidation import (
    INVALIDATION,
    NEVER_SURVIVES,
    SURVIVES_REWORK,
    Effect,
)
from app.engine.orchestrator.machine import (
    TRANSITIONS,
    Transition,
    advance,
    auto_states,
    terminal_states,
)

__all__ = [
    "GUARD_REGISTRY",
    "INVALIDATION",
    "NEVER_SURVIVES",
    "SURVIVES_REWORK",
    "TRANSITIONS",
    "Effect",
    "GuardContext",
    "GuardResult",
    "IllegalTransition",
    "InvalidIdempotencyKey",
    "Transition",
    "advance",
    "auto_states",
    "derive_key",
    "evaluate",
    "terminal_states",
    "validate_client_key",
]
