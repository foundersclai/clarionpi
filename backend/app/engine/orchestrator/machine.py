"""The gate machine as data: the transition table + the single ``advance`` entry point.

This module is **pure** — data + total functions, no DB, no FastAPI. Persistence and the
run coordination described in orchestrator_gates §4 wire in at M1; here the machine is just
"given a state and an event, what is the next state and which guards must hold?".

Edge list is the authoritative one from 01 §4 (state diagram + gate table) plus flow_04's
``registry_bumped`` invalidation edges. See ``TRANSITIONS`` for the complete table.

``registry_bumped`` partition (flow_04 invalidation matrix, as edges):
- **cascade → evidence_review** — from ``plan_review`` / ``drafting`` / ``compliance_review``:
  a bump makes the plan/draft stale, so the matter back-edges to evidence re-confirm.
- **self-loop (absorb in place)** — ``corpus_processing`` / ``analysis_running`` fold new
  facts into the running build; ``facts_review`` / ``strategy_intake`` are pre-freeze (no
  approval exists yet to invalidate); ``evidence_review`` re-presents the gate at the new
  version. ``package_assembly`` CASCADES like drafting/compliance (BUS-05): the live
  builder consumes a FIXED approved draft and does not absorb a newer registry, so a
  self-loop there could publish a stale package — a bump back-edges to ``evidence_review``
  and the in-flight build's completion fence refuses to advance.
- **no registry_bumped edge** — ``package_ready`` artifacts are immutable; a bump there is
  refused with a reason that says so. New records require the EXPLICIT attorney-only
  ``NEW_CYCLE_STARTED`` edge (``package_ready -> evidence_review``, guarded on the registry
  being newer than the packaged draft) — so ``package_ready`` is no longer terminal
  (``terminal_states`` is empty), while the prior ``ArtifactSet`` rows stay untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.engine.orchestrator import guards
from app.engine.orchestrator.errors import IllegalTransition
from app.models.enums import GateEvent, GateState


@dataclass(frozen=True)
class Transition:
    """A single edge: the destination state and the guard names that must pass.

    ``guards`` is a tuple of names resolved through ``guards.REGISTRY`` at evaluation time
    (``guards.evaluate``). An empty tuple means an unconditional (system-driven) edge.
    """

    to: GateState
    guards: tuple[str, ...]


_S = GateState
_E = GateEvent

# The COMPLETE edge list. Two groups: (1) the 01 §4 forward + rework edges, (2) flow_04's
# registry_bumped edges (one per state except the immutable package_ready).
TRANSITIONS: Mapping[tuple[GateState, GateEvent], Transition] = {
    # ---- 01 §4: forward path + rework back-edges ---------------------------------------
    (_S.CORPUS_PROCESSING, _E.CORPUS_READY): Transition(_S.FACTS_REVIEW, ()),
    (_S.FACTS_REVIEW, _E.G1_APPROVED): Transition(
        _S.STRATEGY_INTAKE, ("role_attorney", "deadlines_confirmed")
    ),
    (_S.STRATEGY_INTAKE, _E.G15_SUBMITTED): Transition(
        _S.ANALYSIS_RUNNING, ("role_attorney", "budget_available")
    ),
    (_S.ANALYSIS_RUNNING, _E.ANALYSIS_COMPLETE): Transition(_S.EVIDENCE_REVIEW, ()),
    # G2a confirm freezes/pins the registry version (flow_02 freeze); the pin itself is
    # recorded by the caller — the guard here enforces attorney + high-severity disposition.
    (_S.EVIDENCE_REVIEW, _E.G2A_CONFIRMED): Transition(
        _S.PLAN_REVIEW, ("role_attorney", "high_severity_dispositioned_or_override")
    ),
    # Late records / re-picks WHILE reviewing evidence = rebuild (re-run analysis).
    (_S.EVIDENCE_REVIEW, _E.PICKS_CHANGED): Transition(_S.ANALYSIS_RUNNING, ()),
    (_S.EVIDENCE_REVIEW, _E.DOCUMENTS_UPLOADED): Transition(_S.ANALYSIS_RUNNING, ()),
    (_S.PLAN_REVIEW, _E.G25_APPROVED): Transition(
        _S.DRAFTING, ("role_attorney", "registry_version_match", "budget_available")
    ),
    (_S.PLAN_REVIEW, _E.STRATEGY_REVISED): Transition(_S.STRATEGY_INTAKE, ("role_attorney",)),
    (_S.DRAFTING, _E.DRAFT_COMPLETE): Transition(_S.COMPLIANCE_REVIEW, ()),
    (_S.COMPLIANCE_REVIEW, _E.SEMANTIC_FINDING_REGEN): Transition(_S.DRAFTING, ()),
    (_S.COMPLIANCE_REVIEW, _E.G3_APPROVED): Transition(
        _S.PACKAGE_ASSEMBLY,
        ("role_attorney", "registry_version_match", "no_blocking_findings"),
    ),
    (_S.PACKAGE_ASSEMBLY, _E.ARTIFACTS_BUILT): Transition(_S.PACKAGE_READY, ()),
    # ---- flow_04: registry_bumped invalidation edges -----------------------------------
    # Cascade: plan/draft stale -> land back in evidence_review for delta re-confirm.
    (_S.PLAN_REVIEW, _E.REGISTRY_BUMPED): Transition(_S.EVIDENCE_REVIEW, ()),
    (_S.DRAFTING, _E.REGISTRY_BUMPED): Transition(_S.EVIDENCE_REVIEW, ()),
    (_S.COMPLIANCE_REVIEW, _E.REGISTRY_BUMPED): Transition(_S.EVIDENCE_REVIEW, ()),
    # Absorb-in-place self-loops (running build / pre-freeze / re-present-in-place).
    (_S.CORPUS_PROCESSING, _E.REGISTRY_BUMPED): Transition(_S.CORPUS_PROCESSING, ()),
    (_S.ANALYSIS_RUNNING, _E.REGISTRY_BUMPED): Transition(_S.ANALYSIS_RUNNING, ()),
    (_S.FACTS_REVIEW, _E.REGISTRY_BUMPED): Transition(_S.FACTS_REVIEW, ()),
    (_S.STRATEGY_INTAKE, _E.REGISTRY_BUMPED): Transition(_S.STRATEGY_INTAKE, ()),
    (_S.EVIDENCE_REVIEW, _E.REGISTRY_BUMPED): Transition(_S.EVIDENCE_REVIEW, ()),
    # package_assembly consumes a FIXED approved draft — a bump cascades like drafting/
    # compliance (BUS-05); the build-completion fence refuses the stale forward advance.
    (_S.PACKAGE_ASSEMBLY, _E.REGISTRY_BUMPED): Transition(_S.EVIDENCE_REVIEW, ()),
    # package_ready: NO registry_bumped edge — immutable; the explicit cycle start below is
    # the ONLY way out (attorney-only, and only when the registry outran the packaged draft).
    (_S.PACKAGE_READY, _E.NEW_CYCLE_STARTED): Transition(
        _S.EVIDENCE_REVIEW, ("role_attorney", "registry_newer_than_packaged_draft")
    ),
}


# No state is terminal anymore (BUS-05): package_ready exits ONLY via the guarded
# NEW_CYCLE_STARTED edge — its artifacts stay immutable; the matter does not.
terminal_states: frozenset[GateState] = frozenset()

# System-driven states: the orchestrator advances these on a background-job signal, not an
# attorney action. The FE keys on the ``isRunning`` pattern here (orchestrator_gates §4:
# currentStep stays on the owning gate; the FE keys on isRunning, not step churn).
auto_states: frozenset[GateState] = frozenset(
    {
        GateState.CORPUS_PROCESSING,
        GateState.ANALYSIS_RUNNING,
        GateState.DRAFTING,
        GateState.PACKAGE_ASSEMBLY,
    }
)


def advance(state: GateState, event: GateEvent) -> Transition:
    """Return the ``Transition`` for ``(state, event)``, or raise ``IllegalTransition``.

    This is a pure table lookup: it does NOT evaluate guards (that is ``guards.evaluate``
    against a ``GuardContext``) and does NOT mutate anything. Every ``(state, event)`` pair
    has exactly one outcome — a mapped ``Transition`` or an ``IllegalTransition`` — never a
    third result and never a crash (test_machine_exhaustive locks this over the 10×14 grid).
    """
    transition = TRANSITIONS.get((state, event))
    if transition is not None:
        return transition

    if state is GateState.PACKAGE_READY and event is GateEvent.REGISTRY_BUMPED:
        reason = "package is immutable — new records start a new draft cycle (flow_04)"
    else:
        reason = f"no transition defined from {state.value} on {event.value}"
    raise IllegalTransition(state, event, reason)


def _assert_guard_names_resolve() -> None:
    """Fail loud at import time if the table names a guard absent from ``guards.REGISTRY``.

    This locks the machine table and the guard registry together: adding a guard name to a
    ``Transition`` without implementing it (or renaming a guard) breaks import immediately,
    not at some later runtime evaluation.
    """
    used = {name for transition in TRANSITIONS.values() for name in transition.guards}
    missing = used - set(guards.REGISTRY)
    assert not missing, f"transition table references unknown guard(s): {sorted(missing)}"


_assert_guard_names_resolve()
