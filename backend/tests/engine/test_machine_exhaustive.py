"""Exhaustive grid over the 10×14 (state, event) product.

Locks the transition table against silent edits: every pair either maps to a ``Transition``
or raises ``IllegalTransition`` — no third outcome, no crash. The mapped-edge count is
asserted exactly.
"""

from __future__ import annotations

import itertools

import pytest

from app.engine.orchestrator import guards
from app.engine.orchestrator.errors import IllegalTransition
from app.engine.orchestrator.machine import TRANSITIONS, advance, terminal_states
from app.models.enums import GateEvent, GateState

# 13 core (01 §4 forward + rework) + 9 registry_bumped (all states but package_ready) = 22.
EXPECTED_MAPPED_EDGES = 22

ALL_PAIRS = list(itertools.product(GateState, GateEvent))


def test_grid_is_ten_by_fourteen() -> None:
    # Guards the premise of this file: 10 states × 14 events.
    assert len(GateState) == 10
    assert len(GateEvent) == 14
    assert len(ALL_PAIRS) == 140


def test_mapped_edge_count_is_exact() -> None:
    # Recompute from the table and pin it; a stray added/removed edge fails here.
    assert len(TRANSITIONS) == EXPECTED_MAPPED_EDGES


@pytest.mark.parametrize(("state", "event"), ALL_PAIRS)
def test_every_pair_maps_or_raises(state: GateState, event: GateEvent) -> None:
    """No third outcome: either TRANSITIONS has the edge or advance() raises IllegalTransition."""
    if (state, event) in TRANSITIONS:
        transition = advance(state, event)
        assert transition is TRANSITIONS[(state, event)]
        assert isinstance(transition.to, GateState)
    else:
        with pytest.raises(IllegalTransition) as excinfo:
            advance(state, event)
        # The error carries the offending pair for the caller/audit path.
        assert excinfo.value.state is state
        assert excinfo.value.event is event
        assert excinfo.value.reason


def test_every_mapped_guard_name_exists_in_registry() -> None:
    used = {name for transition in TRANSITIONS.values() for name in transition.guards}
    assert used, "sanity: the table should reference at least one guard"
    assert used <= set(guards.REGISTRY)


def test_package_ready_is_terminal_no_outgoing_edges() -> None:
    outgoing = [pair for pair in TRANSITIONS if pair[0] is GateState.PACKAGE_READY]
    assert outgoing == []
    assert GateState.PACKAGE_READY in terminal_states
    # And it is the ONLY terminal state.
    assert terminal_states == frozenset({GateState.PACKAGE_READY})


def test_package_ready_registry_bump_reason_mentions_immutable() -> None:
    with pytest.raises(IllegalTransition) as excinfo:
        advance(GateState.PACKAGE_READY, GateEvent.REGISTRY_BUMPED)
    assert "immutable" in excinfo.value.reason.lower()


def test_registry_bumped_maps_for_every_state_except_package_ready() -> None:
    # flow_04: one registry_bumped edge per state, package_ready excluded.
    bumpable = {s for s in GateState if s is not GateState.PACKAGE_READY}
    mapped = {s for s in GateState if (s, GateEvent.REGISTRY_BUMPED) in TRANSITIONS}
    assert mapped == bumpable
    assert (GateState.PACKAGE_READY, GateEvent.REGISTRY_BUMPED) not in TRANSITIONS
