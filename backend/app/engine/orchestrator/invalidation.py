"""flow_04's invalidation matrix and rework-survival sets, encoded as data.

When the fact registry bumps (late records arrive) while a matter sits in some gate state,
the orchestrator applies flow_04's matrix keyed on the *current* state. This module encodes
that matrix — and the "what survives / never survives rework" lists — as constants so M1's
persistence/UI wiring implements against data, not prose.

The machine's ``registry_bumped`` edges (``machine.TRANSITIONS``) are the *state change*;
``INVALIDATION`` here is the *effect classification* the UI renders alongside it (banner /
diff / hard-block copy). They are consistent by construction:

| state              | edge (machine)        | effect (this module)        |
|--------------------|-----------------------|-----------------------------|
| evidence_review    | self-loop             | re_present_with_diff        |
| plan_review        | -> evidence_review    | plan_stale_back_to_evidence |
| drafting           | -> evidence_review    | draft_stale_g3_blocked      |
| compliance_review  | -> evidence_review    | draft_stale_g3_blocked      |
| package_assembly   | -> evidence_review    | draft_stale_g3_blocked      |
| package_ready      | (refused; explicit    | immutable_new_cycle         |
|                    |  NEW_CYCLE_STARTED)   |                             |
| corpus_processing  | self-loop             | absorb_in_progress          |
| analysis_running   | self-loop             | absorb_in_progress          |
| facts_review       | self-loop             | absorb_in_progress          |
| strategy_intake    | self-loop             | absorb_in_progress          |

``package_assembly`` cascades (BUS-05): the live package builder consumes a FIXED approved
draft (``package/build.py`` keys artifacts to the draft's old version) and does not absorb
a newer registry — a self-loop there could publish a stale package.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from app.models.enums import GateState


class Effect(StrEnum):
    """The user-visible consequence of a registry bump in a given state (flow_04 matrix)."""

    RE_PRESENT_WITH_DIFF = "re_present_with_diff"
    PLAN_STALE_BACK_TO_EVIDENCE = "plan_stale_back_to_evidence"
    DRAFT_STALE_G3_BLOCKED = "draft_stale_g3_blocked"
    IMMUTABLE_NEW_CYCLE = "immutable_new_cycle"
    ABSORB_IN_PROGRESS = "absorb_in_progress"


# Covers ALL ten GateState members (test_invalidation asserts exact coverage).
INVALIDATION: Mapping[GateState, Effect] = {
    # Still in prep at the frozen gate — fold the delta in, re-present at the new version.
    GateState.EVIDENCE_REVIEW: Effect.RE_PRESENT_WITH_DIFF,
    # Plan bound to the old version is stale — back-edge to evidence re-confirm.
    GateState.PLAN_REVIEW: Effect.PLAN_STALE_BACK_TO_EVIDENCE,
    # Draft bound to the old version is stale; G3 is hard-blocked on version mismatch.
    GateState.DRAFTING: Effect.DRAFT_STALE_G3_BLOCKED,
    GateState.COMPLIANCE_REVIEW: Effect.DRAFT_STALE_G3_BLOCKED,
    # The in-progress build consumes a FIXED approved draft — same stale-draft cascade
    # (BUS-05); the build-completion fence refuses the stale forward advance.
    GateState.PACKAGE_ASSEMBLY: Effect.DRAFT_STALE_G3_BLOCKED,
    # Delivered artifacts are frozen — a bump starts a fresh (v2) draft cycle.
    GateState.PACKAGE_READY: Effect.IMMUTABLE_NEW_CYCLE,
    # Auto/in-progress ingest/analysis states — fold the new facts into the running build
    # (these genuinely absorb: they re-read the registry as they run).
    GateState.CORPUS_PROCESSING: Effect.ABSORB_IN_PROGRESS,
    GateState.ANALYSIS_RUNNING: Effect.ABSORB_IN_PROGRESS,
    # Pre-freeze gates: facts arrive before any approval exists, so nothing to invalidate.
    GateState.FACTS_REVIEW: Effect.ABSORB_IN_PROGRESS,
    GateState.STRATEGY_INTAKE: Effect.ABSORB_IN_PROGRESS,
}


# flow_04 §"What survives / never survives rework". Encoded as data (not prose) so M1's
# rework path reapplies/re-earns against these constants.
#
# Survives (reapplied over rebuilt derived state): chronology overlays reapply over rebuilt
# rows; dispositions for UNCHANGED flags persist; picks persist for UNCHANGED exhibits.
SURVIVES_REWORK: frozenset[str] = frozenset(
    {
        "chronology_overlays",
        "dispositions_unchanged_flags",
        "exhibit_picks_unchanged",
    }
)

# Never survives silently (must be re-earned): plan approval (G2.5) re-approved; the draft
# regens against the new registry; G3 findings are re-checked at the new version.
NEVER_SURVIVES: frozenset[str] = frozenset(
    {
        "plan_approval",
        "draft",
        "open_g3_findings",
    }
)
