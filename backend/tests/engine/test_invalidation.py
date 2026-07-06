"""Invalidation-matrix coverage + rework-survival set tests (flow_04)."""

from __future__ import annotations

from app.engine.orchestrator.invalidation import (
    INVALIDATION,
    NEVER_SURVIVES,
    SURVIVES_REWORK,
    Effect,
)
from app.engine.orchestrator.machine import TRANSITIONS
from app.models.enums import GateEvent, GateState


def test_invalidation_covers_all_ten_states_exactly() -> None:
    assert set(INVALIDATION) == set(GateState)
    assert len(INVALIDATION) == 10


def test_named_effects_spot_assert() -> None:
    assert INVALIDATION[GateState.EVIDENCE_REVIEW] == Effect.RE_PRESENT_WITH_DIFF
    assert INVALIDATION[GateState.PLAN_REVIEW] == Effect.PLAN_STALE_BACK_TO_EVIDENCE
    assert INVALIDATION[GateState.DRAFTING] == Effect.DRAFT_STALE_G3_BLOCKED
    assert INVALIDATION[GateState.COMPLIANCE_REVIEW] == Effect.DRAFT_STALE_G3_BLOCKED
    assert INVALIDATION[GateState.PACKAGE_READY] == Effect.IMMUTABLE_NEW_CYCLE


def test_pre_freeze_and_auto_states_absorb() -> None:
    for state in (
        GateState.CORPUS_PROCESSING,
        GateState.ANALYSIS_RUNNING,
        GateState.PACKAGE_ASSEMBLY,
        GateState.FACTS_REVIEW,
        GateState.STRATEGY_INTAKE,
    ):
        assert INVALIDATION[state] == Effect.ABSORB_IN_PROGRESS


def test_survives_and_never_survives_are_disjoint() -> None:
    assert SURVIVES_REWORK.isdisjoint(NEVER_SURVIVES)


def test_survival_sets_have_expected_members() -> None:
    assert SURVIVES_REWORK == {
        "chronology_overlays",
        "dispositions_unchanged_flags",
        "exhibit_picks_unchanged",
    }
    assert NEVER_SURVIVES == {"plan_approval", "draft", "open_g3_findings"}


def test_invalidation_effect_agrees_with_machine_edge() -> None:
    """The effect classification and the machine's registry_bumped edge stay consistent.

    - immutable_new_cycle  <-> no registry_bumped edge (package_ready).
    - back-to-evidence effects <-> edge lands in evidence_review (from a different state).
    - absorb / re-present    <-> self-loop edge (edge target == the state itself).
    """
    for state, effect in INVALIDATION.items():
        edge = TRANSITIONS.get((state, GateEvent.REGISTRY_BUMPED))
        if effect == Effect.IMMUTABLE_NEW_CYCLE:
            assert edge is None
        elif effect in (
            Effect.PLAN_STALE_BACK_TO_EVIDENCE,
            Effect.DRAFT_STALE_G3_BLOCKED,
        ):
            assert edge is not None
            assert edge.to == GateState.EVIDENCE_REVIEW
            assert state != GateState.EVIDENCE_REVIEW
        else:  # RE_PRESENT_WITH_DIFF or ABSORB_IN_PROGRESS -> self-loop
            assert edge is not None
            assert edge.to == state
