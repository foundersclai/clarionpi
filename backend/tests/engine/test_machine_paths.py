"""Golden-path and rework walks through the machine (advance() only, no guard eval)."""

from __future__ import annotations

from app.engine.orchestrator.machine import advance
from app.models.enums import GateEvent as E
from app.models.enums import GateState as S


def test_golden_path_corpus_to_package_ready() -> None:
    """Full forward walk, asserting each hop lands where 01 §4 says."""
    hops = [
        (S.CORPUS_PROCESSING, E.CORPUS_READY, S.FACTS_REVIEW),
        (S.FACTS_REVIEW, E.G1_APPROVED, S.STRATEGY_INTAKE),
        (S.STRATEGY_INTAKE, E.G15_SUBMITTED, S.ANALYSIS_RUNNING),
        (S.ANALYSIS_RUNNING, E.ANALYSIS_COMPLETE, S.EVIDENCE_REVIEW),
        (S.EVIDENCE_REVIEW, E.G2A_CONFIRMED, S.PLAN_REVIEW),
        (S.PLAN_REVIEW, E.G25_APPROVED, S.DRAFTING),
        (S.DRAFTING, E.DRAFT_COMPLETE, S.COMPLIANCE_REVIEW),
        (S.COMPLIANCE_REVIEW, E.G3_APPROVED, S.PACKAGE_ASSEMBLY),
        (S.PACKAGE_ASSEMBLY, E.ARTIFACTS_BUILT, S.PACKAGE_READY),
    ]
    state = S.CORPUS_PROCESSING
    for from_state, event, expected in hops:
        assert state == from_state
        state = advance(state, event).to
        assert state == expected
    assert state == S.PACKAGE_READY


def test_rework_evidence_review_picks_changed_goes_to_analysis() -> None:
    assert advance(S.EVIDENCE_REVIEW, E.PICKS_CHANGED).to == S.ANALYSIS_RUNNING


def test_rework_evidence_review_documents_uploaded_goes_to_analysis() -> None:
    assert advance(S.EVIDENCE_REVIEW, E.DOCUMENTS_UPLOADED).to == S.ANALYSIS_RUNNING


def test_rework_compliance_semantic_finding_goes_to_drafting() -> None:
    assert advance(S.COMPLIANCE_REVIEW, E.SEMANTIC_FINDING_REGEN).to == S.DRAFTING


def test_rework_plan_review_strategy_revised_goes_to_strategy_intake() -> None:
    assert advance(S.PLAN_REVIEW, E.STRATEGY_REVISED).to == S.STRATEGY_INTAKE


def test_registry_bump_from_drafting_cascades_to_evidence_review() -> None:
    assert advance(S.DRAFTING, E.REGISTRY_BUMPED).to == S.EVIDENCE_REVIEW


def test_registry_bump_cascade_states_all_land_in_evidence_review() -> None:
    for state in (S.PLAN_REVIEW, S.DRAFTING, S.COMPLIANCE_REVIEW):
        assert advance(state, E.REGISTRY_BUMPED).to == S.EVIDENCE_REVIEW


def test_registry_bump_self_loop_states_stay_put() -> None:
    for state in (
        S.CORPUS_PROCESSING,
        S.ANALYSIS_RUNNING,
        S.FACTS_REVIEW,
        S.STRATEGY_INTAKE,
        S.EVIDENCE_REVIEW,
        S.PACKAGE_ASSEMBLY,
    ):
        assert advance(state, E.REGISTRY_BUMPED).to == state
