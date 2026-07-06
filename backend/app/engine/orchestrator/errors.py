"""Typed errors raised by the orchestrator gate machine.

These carry structured fields (not just a message) so callers — the API layer at M1,
the audit path, tests — can branch on the offending ``state``/``event`` or the rejected
idempotency key without string-parsing. Pure module: no DB, no FastAPI imports.
"""

from __future__ import annotations

from app.models.enums import GateEvent, GateState


class IllegalTransition(Exception):
    """Raised when ``(state, event)`` is not a mapped edge of the gate machine.

    The machine refuses the pair, writes no transition, and leaves state unchanged
    (orchestrator_gates §4: "Illegal (state, event) pairs return a typed error, write no
    transition, and leave state unchanged"). ``reason`` is human-typed for the wire/UI.
    """

    def __init__(self, state: GateState, event: GateEvent, reason: str) -> None:
        self.state = state
        self.event = event
        self.reason = reason
        super().__init__(f"illegal transition from {state.value} on {event.value}: {reason}")


class InvalidIdempotencyKey(ValueError):
    """Raised when a client-supplied idempotency key fails format validation.

    Carries the rejected ``key`` and a human ``reason`` (bad length / bad characters) so
    the API layer can echo a precise 4xx without re-deriving the rule.
    """

    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"invalid idempotency key: {reason}")
