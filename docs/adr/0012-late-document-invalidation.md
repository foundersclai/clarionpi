# ADR-0012 — Late-document invalidation: one bump owner, durable cursor, non-terminal package_ready

Status: accepted · Date: 2026-07-11 · Source: business-completeness audit
(`docs/audit/plans/04-late-document-invalidation.md`, BUS-05)

## Context

New records arriving after early evidence work left derived state silently current:
Phase 0 re-synced the registry but only moved the gate from `corpus_processing` and
`evidence_review`; plans, drafts, compliance results, and package state carried registry
versions with no stale markers; `package_ready` had no way to start the "fresh draft
cycle" the invalidation design required; and package assembly MINTED exhibit tokens,
bumping the registry after the G2a freeze (self-inflicted drift).

## Decision 1 — one bump owner, serialized on the matter row, driven by a durable cursor

`orchestrator/registry_bump.py::apply_registry_bump` is the only place the flow_04 matrix
is applied: it row-locks + REFRESHES the matter, marks EVERY stale plan
(`StrategyPlan.invalidated_by_registry_version`; `approved` survives as historical
evidence but an invalidated approval is never reusable) and supersedes every stale draft
(existing `DraftStatus.SUPERSEDED`), moves the gate along the `REGISTRY_BUMPED` edge,
audits (`from_registry_version` = the LOCKED cursor, never caller-supplied), advances
`Matter.invalidation_applied_registry_version`, and commits — one transaction; a failure
leaves the cursor behind so a no-pending-document retry re-attempts the invalidation.

**Gate actions take the same lock first**: `apply_gate_action` now begins by locking +
refreshing the matter row (`populate_existing`), so a bump and a human approve serialize
rather than overwriting each other's gate moves — this changes the service step order and
is pinned here. The Phase-0 completion handler
(`orchestrator/phase0_completion.py`, INJECTED into `run_phase0` by the API layer so
corpus no longer imports the machine) branches on the state that actually serialized.
Legacy NULL-cursor matters are never grandfathered: `reconcile_matter_cursor` evaluates
their derived state on first touch and applies any missed invalidation before
initializing the cursor.

## Decision 2 — `package_assembly` cascades; completions are fenced

The live builder consumes a FIXED approved draft (artifacts key to the draft's version),
so `PACKAGE_ASSEMBLY + REGISTRY_BUMPED` moved from `absorb_in_progress` to the same
stale-draft cascade as drafting/compliance (matrix effect `DRAFT_STALE_G3_BLOCKED`).
Demand and package completions re-lock the matter immediately before their advances and
require: gate unchanged, draft non-superseded, plan non-invalidated, draft/plan registry
== matter registry — otherwise the existing typed `draft_registry_drift` error is
emitted and the invalidation's back-edge stands. A committed artifact set stays as
immutable HISTORICAL output. There is ONE current-draft selector
(`compliance.engine.latest_draft`, re-used by the drafting routes): the highest version
only if non-superseded, `None` otherwise — a stale v1 can never become current again
after v2 was invalidated.

## Decision 3 — exhibit tokens settle at G2a confirm; the package build is read-only

`build_artifact_set` used to call the manifest with `mint_tokens=True`, bumping the
registry DURING package assembly — making a normal first build self-drift the version
fence. Settlement moved into the G2a confirm side effect
(`_settle_exhibits_then_freeze`): mint all valid manifest EX tokens (tokenizer path runs
`commit=False` — caller-owned transaction), advance the cursor to the settled version,
THEN freeze. Package assembly consumes settled tokens read-only
(`require_settled_tokens=True`) and fails typed (`ExhibitTokenUnsettled` →
`exhibit_tokens_unsettled` SSE) on a missing/drifted token. The manifest GET is
read-only at every gate (the `?mint=true` write-on-GET is gone), so no caller can bump
the registry outside the locked settlement or Phase-0 invalidation.

## Decision 4 — `package_ready` is non-terminal via an explicit, guarded cycle start

`GateEvent.NEW_CYCLE_STARTED` / `GateAction.START_CYCLE` transition
`package_ready → evidence_review` — attorney-only, guarded by
`registry_newer_than_packaged_draft` (the matter registry must have outrun the latest
packaged set), routed through the SAME idempotent gate-submit service (GateRecord +
audit + single commit). `terminal_states` is now EMPTY. Because a successful cycle start
moves the state, its idempotent replay runs BEFORE the gate-state check (a retrying
client replays the original record instead of `gate_state_mismatch`) — a deliberate,
START_CYCLE-scoped revision of the global replay ordering; every other action keeps the
old order. Prior `ArtifactSet` rows and bytes are never mutated; the package view
carries explicit `registry_version_current` / `new_cycle_required` / per-set `current`
fields so the UI displays state rather than inferring it.
