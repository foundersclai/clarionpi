# Late Document Invalidation Implementation Plan

Findings covered: `BUS-05`

## Goal

When new records arrive after early evidence work, derived plans, drafts, compliance results, and
package state cannot remain silently stale.

## Current State

- `backend/app/engine/orchestrator/invalidation.py` already encodes an invalidation matrix for all
  gate states.
- `backend/app/engine/orchestrator/machine.py` already has `REGISTRY_BUMPED` edges for
  `plan_review`, `drafting`, `compliance_review`, and self-loop states.
- `backend/app/corpus/ingest/phase0.py` only moves the gate for:
  - `corpus_processing -> facts_review`
  - `evidence_review -> analysis_running` via `DOCUMENTS_UPLOADED`
- For other late-document states, `phase0.py` records `phase0_late_documents_processed` and leaves
  the gate untouched.
- `StrategyPlan`, `DemandDraft`, `DraftSection`, and `ComplianceFinding` carry registry versions,
  but there is no explicit stale marker for plan approval or compliance results.
- `package_ready` has no transition or API action that starts the "fresh draft cycle" required by
  the invalidation design, so merely showing a banner would leave the matter stuck.

## Non-Goals

- Do not rebuild all downstream artifacts synchronously inside Phase 0.
- Do not mutate immutable package-ready artifacts in place.
- Do not discard attorney edits, risk-flag dispositions, chronology overlays, or exhibit picks that
  the invalidation matrix says can survive rework.

## Implementation Steps

### 0. Reproduce and instrument the silent stale-state failure first

Files:

- `backend/app/corpus/ingest/phase0.py`
- `backend/tests/corpus/test_phase0.py`
- `backend/tests/api/test_m5_exit_flow.py`

Plan:

1. Add regression tests for the desired behavior first: process a registry-changing late document
   from `plan_review`, `drafting`, `compliance_review`, and `package_assembly`, then assert the
   required back-edge/stale markers. Run them before the fix and confirm they fail because the
   gate/derived rows incorrectly remain current.
2. Before changing invalidation logic, add temporary diagnostic logging at Phase 0 completion with
   the matter id, current gate, registry version, latest plan id/version/approval, latest draft
   id/version/status, and latest artifact-set registry version. Run the reproduction and retain the
   captured log evidence in the PR description to confirm that registry sync committed while the
   downstream state stayed current.
3. Remove the temporary diagnostic fields after the hypothesis is confirmed, or keep only a
   non-PHI debug-level summary if it has durable operational value. Do not log document contents,
   client names, rendered draft text, or other PHI.

### 1. Add a registry-bump orchestration service

Files:

- `backend/app/engine/orchestrator/registry_bump.py`
- `backend/app/engine/orchestrator/invalidation.py`
- `backend/app/engine/orchestrator/machine.py`
- `backend/app/engine/orchestrator/service.py`
- `backend/tests/engine/test_invalidation.py`
- `backend/tests/engine/test_machine_paths.py` (its registry-bump self-loop test at :56-65
  asserts `PACKAGE_ASSEMBLY` stays put and must move to the new back-edge)
- `backend/tests/engine/test_registry_bump.py`
- `backend/tests/engine/test_gate_service.py`

Plan:

1. Create a service function such as:

   ```python
   apply_registry_bump(db, *, matter, user, to_registry_version)
   ```

2. The service should:
   - lock and refresh the `Matter` row before deciding the effect, so a concurrent gate action or
     package/demand completion cannot be silently overwritten
   - make human gate submissions participate in the same serialization discipline: at the start of
     `apply_gate_action`, acquire and refresh the same `Matter` row lock before evaluating the
     submitted gate, idempotency, payload version, guards, or side effects. A registry bump can
     otherwise commit `evidence_review` after the gate request loaded the old row, then the unlocked
     gate transaction can approve against its stale identity-map values and overwrite the back-edge
     with a forward state. This changes the service step order, so pin it in the orchestrator
     contract/ADR updates already scheduled in Rollout step 6.
   - use a durable `Matter.invalidation_applied_registry_version` cursor (added in step 2), not only
     the Phase 0 run's starting version; registry sync commits before the gate step, so a crash in
     between must be recoverable on retry
   - derive the audit's `from_registry_version` from the locked cursor rather than trusting a
     caller-supplied value
   - return an idempotent no-op when the requested target version is already covered by the cursor
   - read the refreshed current `GateState`
   - look up `INVALIDATION[state]`
   - apply `machine.advance(state, GateEvent.REGISTRY_BUMPED)` when an edge exists
   - invalidate all plans/drafts made stale by the target registry version before moving the gate
   - record an audit event with the old state, new state, effect, and registry versions
   - update the cursor, stale markers, gate state, and audit row in one transaction; a failure must
     leave the cursor behind so the next Phase 0 retry re-attempts invalidation
   - leave package-ready immutable, returning an explicit `immutable_new_cycle` effect rather than
     attempting a transition (`machine.advance` raises `IllegalTransition` for
     `PACKAGE_READY + REGISTRY_BUMPED` — `machine.py:125`; `INVALIDATION[GateState.PACKAGE_READY]`
     is already `Effect.IMMUTABLE_NEW_CYCLE`)
3. Change `PACKAGE_ASSEMBLY + REGISTRY_BUMPED` from `absorb_in_progress` to the same stale-draft
   back-edge used by drafting/compliance. The live package builder consumes a fixed approved draft
   and does not absorb a newer registry (`package/build.py` keys artifacts to the draft's old
   version), so a self-loop can otherwise publish a stale package. Update the machine tests this
   changes: drop `PACKAGE_ASSEMBLY` from the self-loop list in
   `test_machine_paths.py:56-65` and adjust `test_invalidation.py:32,36`. Also update the
   behavior-describing docstrings that assert the old semantics — `machine.py:16-19`
   ("package_assembly also self-loops … absorb_in_progress") and the docstring matrix plus the
   "Auto/in-progress build states" grouping in `invalidation.py:21` and `:55-58`.
4. Keep evidence-review's existing `DOCUMENTS_UPLOADED -> ANALYSIS_RUNNING` behavior unless product
   owners decide to align it with the self-loop matrix in a separate change.
5. Add exact-state tests for every `GateState`, including idempotent replay and a retry after a
   simulated failure between registry sync and invalidation.

### 2. Add stale/superseded markers where persisted rows need them

Files:

- `backend/app/models/enums.py`
- `backend/app/engine/orchestrator/machine.py` (the new `package_ready -> evidence_review` edge +
  `terminal_states`)
- `backend/app/engine/orchestrator/guards.py` (register the new cycle-start guard; see item 8)
- `backend/tests/engine/test_machine_exhaustive.py`
- `backend/tests/models/test_enums.py` (pins the exact `GateEvent` list and `GateAction` value set)
- `backend/app/models/orm.py`
- `backend/app/models/schemas.py`
- `backend/alembic/versions/<new>_derived_state_staleness.py` (hand-written like existing
  revisions; current head is `0009_artifact_sets` — re-resolve `down_revision` at implementation
  time because the auth-hardening and upload-safety plans also add migrations)
- `backend/app/api/view_models.py`
- `backend/app/engine/brain2/generate.py`
- `backend/app/engine/compliance/engine.py`
- `backend/app/engine/orchestrator/service.py`
- `backend/app/engine/tokenizer/registry.py`
- `backend/app/package/manifest.py`
- `backend/app/package/build.py`
- `backend/app/api/routes/evidence.py`
- `backend/app/api/routes/gates.py`
- `backend/app/api/routes/drafting.py`
- `backend/tests/engine/test_gate_service.py`
- `backend/tests/package/test_manifest.py`
- `backend/tests/package/test_build.py`
- `backend/tests/api/test_evidence_api.py`
- `backend/tests/api/test_gates_api.py`
- `frontend/components/evidence-workbench.tsx`
- `frontend/lib/evidence.ts`
- `frontend/lib/types.ts`

Plan:

1. Add `Matter.invalidation_applied_registry_version: int` as the durable recovery cursor. Do not
   blindly backfill every existing matter to its current `registry_version`: that would grandfather
   matters already left stale by this bug. Add a one-time reconciliation path that scans each
   matter's current gate plus latest plan/draft/artifact-set registry versions, applies the same
   invalidation service where downstream state is older than the matter registry, and only then
   sets the cursor to the reconciled current version. Matters with no derived-version mismatch may
   be initialized directly to the current registry version. Make the field non-null only after this
   data reconciliation, and add migration/reconciliation tests for both stale and already-current
   legacy rows.
2. Add `StrategyPlan.invalidated_by_registry_version: int | None`.
3. Preserve the existing `approved` boolean as historical approval evidence, but treat a plan as
   currently usable only when `approved is True` and `invalidated_by_registry_version is None`.
   Apply the marker to every plan whose `registry_version` is older than the target version, not
   only one latest/approved row, and update approved-plan selection in Brain-2 plus G2.5 approval
   checks so an invalidated approval can never be reused.
4. Use existing `DraftStatus.SUPERSEDED` for every non-superseded draft whose `registry_version` is
   older than the target version. When a new draft is created, supersede prior active drafts in the
   same transaction so there is at most one current draft.
5. Do not implement "latest non-superseded" as a selector that falls back to an older draft. Define
   one shared current-draft selector: inspect the highest version and return it only when its status
   is not `superseded`; if the highest version is superseded, return `None`. This prevents a stale
   v1 from becoming current again after v2 is invalidated.
6. Do not add a new `ComplianceFinding` status in the first implementation. Instead, update current
   finding queries and blockers to use the shared current-draft selector — today
   `latest_draft` is plain `max(version)` (`engine/compliance/engine.py:471-482`) and
   `open_blocking_count` scopes by `draft_id` (`engine.py:450-468`), with consumers at
   `engine/orchestrator/service.py:335-337`, `api/routes/drafting.py:247`,
   `api/view_models.py:535` (`compliance_review_vm`'s draft selector), `api/view_models.py:586`,
   and `api/view_models.py:647` (`package_vm` buildability). Note that
   `api/routes/drafting.py:599-606` carries a duplicate `_latest_draft` (used by the package-build
   path at `drafting.py:405`) whose docstring asserts equivalence with the compliance one — update
   both selectors (prefer unifying them) so superseded drafts are excluded everywhere, and add
   tests with multiple historical drafts proving an older draft/findings cannot fall back into the
   guard, package build, or current view after the highest version is superseded.
7. Preserve historical records for audit; do not delete old plans, drafts, sections, findings, or
   artifact sets.
8. Add the explicit package-ready cycle-start contract: `GateEvent.NEW_CYCLE_STARTED` and
   `GateAction.START_CYCLE` (or an equivalently typed action) transition `package_ready ->
   evidence_review`, attorney-only, only when the matter registry is newer than the packaged draft.
   Route it through the existing idempotent gate-submit service/route (not an ad hoc drafting
   endpoint) so it writes the required `GateRecord` and audit in the same transaction. It must not
   mutate or delete the prior `ArtifactSet`. This makes `package_ready` non-terminal: update
   `terminal_states` (`machine.py:98`, becomes empty) and the module docstring, and re-pin the
   machine unit tests — `len(GateEvent) == 14` → 15 and `ALL_PAIRS` 140 → 150
   (`test_machine_exhaustive.py:27-29`), `EXPECTED_MAPPED_EDGES = 22` → 23 (constant at `:20`,
   asserted at `:34`), and `test_package_ready_is_terminal_no_outgoing_edges` (`:59-64`), which
   must instead assert the single guarded cycle-start edge. Also re-pin the enum-contract test
   `tests/models/test_enums.py`: `len(values) == 14` and the ordered `GateEvent` list (`:29-45`)
   gain `NEW_CYCLE_STARTED`, and the `GateAction` value set
   `{"approve", "reject", "edit", "override"}` (`:52`, asserted at `:129`) gains `START_CYCLE`.
   The current service checks `gate == matter.gate_state` before looking up the idempotency key
   (`service.py:739-757`), so a retry after a successful cycle start reaches `evidence_review` and
   returns `gate_state_mismatch` instead of replaying the original `GateRecord`. Implement an
   explicit replay path for a matching prior `START_CYCLE` submission before that mismatch (or
   deliberately revise the global replay ordering), document the chosen service semantics in the
   required contract/ADR update, and add service + API tests for a post-transition retry. Do not
   claim the action is idempotent based only on duplicate submissions that have not transitioned.
   Implement the "registry newer than the packaged draft" condition as a registered table guard:
   extend `guards.REGISTRY` (`guards.py:146-153`), `GuardContext` (`guards.py:41-48`), and the
   guard-context builder — `machine.py:139-144` asserts at import that every guard name used in
   `TRANSITIONS` resolves in the registry, so an unregistered name fails the whole suite at
   collection.
9. Settle exhibit tokens before the G2a registry freeze, not during package assembly. Today
   `build_artifact_set` calls `build_draft_manifest(..., mint_tokens=True)`
   (`package/build.py:139`), and `mint_exhibits` bumps and commits the matter registry
   (`engine/tokenizer/registry.py:496-531`). That means the step-4 package fence requiring the
   approved draft registry to equal the matter registry would reject an otherwise normal first
   build as soon as package assembly minted its `EX` tokens; it would also make the resulting
   artifact set immediately non-current under the step-5 view-model rule. Refactor the G2a confirm
   side effect so it transactionally mints/updates all valid manifest `EX` tokens first, advances
   `invalidation_applied_registry_version` to the resulting version (the bump is incorporated
   before any downstream plan exists), and then freezes that final registry version. The tokenizer
   entry point used here must support caller-owned transaction control — no internal commit inside
   `apply_gate_action`. Split manifest token settlement from read-only token lookup/stamping, make
   package assembly consume only already-settled tokens, and fail visibly before storing artifacts
   if a required current `EX` token is missing or drifted. `build_artifact_set` must not mutate or
   bump the registry. Remove the ungated write-on-GET behavior from
   `GET /api/matters/{matter_id}/manifest?mint=true` (`api/routes/evidence.py:241-260`): after this
   change the manifest route is read-only at every gate, and the frontend must not offer a button
   that can mint outside G2a confirmation. Otherwise a caller could still bump the registry after
   plan/draft/package approval without passing through either Phase 0 invalidation or the locked
   G2a side effect. Update the route/view-model contract and frontend client accordingly. Add
   regression tests proving G2a settlement + freeze + cursor update are one transaction, the
   manifest GET cannot change the registry at any state, a normal first package build does not
   change `Matter.registry_version`, and the artifact set's registry version equals both the
   approved draft and current matter version.

Tests:

- Registry bump from `plan_review` marks the latest approved plan stale and moves to
  `evidence_review`.
- Registry bump from `drafting` marks the current draft superseded and moves to `evidence_review`.
- Registry bump from `compliance_review` marks the current draft/compliance results superseded and
  moves to `evidence_review`.
- Registry bump from `package_assembly` supersedes the approved draft, blocks package completion,
  and moves to `evidence_review`; any already-written immutable artifact set remains historical and
  cannot advance the gate to `package_ready`.
- Package-ready bump records `immutable_new_cycle` and leaves existing artifact sets untouched.
- Starting the required new cycle is attorney-only/idempotent, moves to `evidence_review`, and
  preserves all prior artifact bytes and rows.
- G2a confirmation settles exhibit tokens before freezing; package assembly performs no token mint
  or registry bump and cannot create an artifact set whose registry label is stale on arrival.

### 3. Wire Phase 0 late-document completion into registry bump handling

Files:

- `backend/app/corpus/ingest/phase0.py`
- `backend/app/api/routes/ingest.py`
- `backend/tests/corpus/test_phase0.py`
- `backend/tests/api/test_m4_exit_flow.py`
- `backend/tests/api/test_m5_exit_flow.py`

Plan:

1. After merge/registry sync, compare the final registry version with the durable
   `invalidation_applied_registry_version` cursor, not merely a run-local pre-run value. This closes
   the crash window created by registry functions committing before Phase 0 reaches its gate step.
   The injected completion handler must acquire and refresh the same `Matter` row lock used by the
   registry-bump and gate-action services before it chooses among the `corpus_processing`,
   `evidence_review`, and other-state branches; do not branch on the `Matter` instance that entered
   the long-running Phase-0 generator. Otherwise an evidence-review approval can serialize during
   ingestion and then be overwritten by a stale `DOCUMENTS_UPLOADED` decision (or approve work that
   should have been routed through the newly observed post-evidence invalidation state).
2. Do not add a direct `app.corpus -> app.engine.orchestrator.registry_bump` import. The ingest
   contract explicitly treats `corpus/` importing `engine/` as a boundary breach
   (`app.corpus.ingest.md:70` and `:113`). Note the "never imports" line is already breached
   today — `phase0.py:69-70` imports `engine.orchestrator.machine` and `engine.tokenizer` — so
   this step must not deepen that existing drift, and moving the gate transitions into the handler
   removes at least the `machine` import. Inject a typed Phase-0 completion callback/handler from
   the API composition layer (or move the completion wrapper wholly into orchestrator ownership);
   the handler owns `CORPUS_READY`, `DOCUMENTS_UPLOADED`, and registry-bump transitions.
3. If the refreshed matter is still in `corpus_processing`, keep the current `CORPUS_READY`
   behavior and advance the durable cursor in the same completion transaction.
4. If the refreshed matter is in `evidence_review` and the cursor lags, keep the current
   `DOCUMENTS_UPLOADED` re-analysis route and advance the cursor atomically.
5. If the refreshed matter is in any other post-corpus state and the cursor lags, call the new
   registry-bump service.
6. Emit an SSE `status` frame with:
   - `state: "registry_bumped"`
   - `effect`
   - `from_gate_state`
   - `to_gate_state`
   - `from_registry_version`
   - `to_registry_version`
7. If the cursor already covers the registry version, keep the existing late-doc processed status
   and avoid invalidating downstream work. A retry after a pre-invalidation crash must instead see
   the lagging cursor and complete the missed invalidation even when no documents remain pending.

Tests:

- Late documents at `plan_review` back-edge to `evidence_review`.
- Late documents at `drafting` back-edge to `evidence_review` and supersede draft state.
- Late documents at `compliance_review` back-edge to `evidence_review` and ensure old G3 findings
  no longer count as current blocking state.
- Late documents at `package_assembly` back-edge to `evidence_review`, supersede the approved draft,
  and cannot finish the old package stream into `package_ready`.
- Late documents at `package_ready` record immutable-new-cycle without modifying existing artifacts.
- A failure after registry sync but before completion invalidation is recovered by a no-pending-doc
  retry using the durable cursor.
- A deterministic evidence-review approval/Phase-0-completion interleaving proves the completion
  handler refreshes under the shared lock and applies the branch for the state that actually
  serialized, without either transaction overwriting the other's gate move from a stale object.

### 4. Fence concurrent forward work against invalidation

Files:

- `backend/app/engine/brain2/generate.py`
- `backend/app/engine/orchestrator/service.py`
- `backend/app/api/routes/drafting.py`
- `backend/app/package/build.py`
- `backend/app/package/manifest.py`
- `backend/tests/engine/test_gate_service.py`
- `backend/tests/api/test_drafting_api.py`
- `backend/tests/api/test_package_api.py`
- `backend/tests/package/test_build.py`

Plan:

1. Immediately before demand generation advances `drafting -> compliance_review`, refresh/lock the
   matter and draft and require: gate still `drafting`, draft not superseded, plan not invalidated,
   and draft/plan registry versions equal the matter registry version. On drift, emit the existing
   typed drift error and do not overwrite the invalidation back-edge.
2. Check the same current-draft and registry conditions both before package work starts and again
   before `ARTIFACTS_BUILT` advances `package_assembly -> package_ready`. Package work must use the
   read-only, already-settled manifest path from step 2.9; it may not mint `EX` tokens or otherwise
   change `Matter.registry_version` between those checks. If invalidation wins the race, do not
   advance; an artifact set already committed remains immutable historical output and is not
   presented as the current package.
3. Add deterministic interleaving tests (no sleeps) that pause completion, apply a registry bump in
   another session, then resume and prove neither demand nor package completion restores the stale
   forward state.
4. Add the equivalent deterministic interleaving test for a human gate submission: pause an
   approval after it has loaded the pre-bump matter, let registry invalidation commit in another
   session, then resume. The shared row-lock protocol must force one transaction to refresh after
   the other; the final state may reflect whichever transaction serialized first, but an approval
   that serialized after the bump must fail/refetch rather than restore a stale forward gate or
   approve an invalidated plan.

### 5. Surface invalidation in frontend gates

Files:

- `frontend/lib/types.ts`
- `frontend/components/gate-stepper.tsx`
- `frontend/components/evidence-workbench.tsx`
- `frontend/components/plan-review-card.tsx`
- `frontend/components/demand-generation-card.tsx`
- `frontend/components/compliance-panel.tsx`
- `frontend/components/package-card.tsx`
- `frontend/lib/gates.ts`
- Corresponding frontend tests

Plan:

1. Add explicit view-model fields, rather than inferring solely from gate copy:
   - plan view: `invalidated_by_registry_version`
   - package view: `registry_version_current` and `new_cycle_required`
   - artifact-set rows: a derived `current` flag (true only for the current, non-superseded draft
     and matching registry version)
2. When the backend moves a matter back to `evidence_review`, make the frontend refetch the gate
   envelope and stop showing stale approve/build actions.
3. For `package_ready`, show that existing artifacts remain downloadable as historical artifacts
   but are not labeled current when `new_cycle_required` is true; new records require a new
   draft/package cycle.
4. When `new_cycle_required` is true, show an attorney-only "Start new cycle" action wired to the
   typed backend action. On success, refetch the gate envelope and land at `evidence_review`; do not
   hide or delete the historical artifact downloads.
5. Keep messages operational, not instructional prose.

## Rollout

1. Add the stale/cursor schema fields and migrations first — the step-1 service reads
   `Matter.invalidation_applied_registry_version`, so the schema must land with or before it.
2. Add the registry-bump service and unit tests for the matrix, landing the shared
   current-draft-selector unification (steps 2.4–2.6) with or before it — the service and the
   later fences both consume that selector. Land the G2a exhibit-token settlement and read-only
   package-manifest split (step 2.9) before enabling the package fences, so package assembly cannot
   self-create registry drift. Then run the one-time legacy-matter reconciliation (it applies this
   service, so it cannot run earlier) and gate traffic/feature enablement until every matter has a
   non-null reconciled cursor.
3. Wire Phase 0 completion to the service without adding a corpus→orchestrator import.
4. Add demand/package completion fences.
5. Land the step-2.8 package-ready cycle-start backend (enum members, the machine edge +
   `terminal_states` + its registered guard, and the gate-service dispatch with post-transition
   replay) with its unit tests, then update API view models and frontend rendering.
6. Update the touched module contracts in the same pass: `app.engine.orchestrator.md`,
   `app.api.view_models.md`, `app.corpus.ingest.md`, and `app.engine.compliance.md` (step 2
   changes the `latest_draft`/`open_blocking_count` guard-feed semantics named in its change rule
   at `app.engine.compliance.md:133-136`), plus `app.engine.tokenizer.md` and
   `app.package.builder.md` for the caller-owned EX settlement/read-only package-build boundary.
   Those change rules also mandate a same-PR `docs/system_contract.md` update, and the
   orchestrator/compliance/package contracts mandate a new ADR
   (`app.engine.orchestrator.md:110-113`, `app.package.builder.md:167-170`) — schedule both.
7. Add end-to-end backend flow tests for late documents in every affected state and the two
   deterministic completion races, the human-gate/invalidation race, plus an
   API/authorization/post-transition-idempotency test for starting the package-ready replacement
   cycle.
8. Update `systemflows/matter_lifecycle.md` and `systemflows/package_assembly.md` for the changed
   `package_assembly` invalidation route and `new_cycle_started` edge, then regenerate the checked-in
   lifecycle/package SVGs. Enum/machine/systemflow vocabulary must remain identical.

## Verification

Run:

```bash
rtk test "cd backend && .venv/bin/pytest -q tests/engine/test_invalidation.py tests/engine/test_registry_bump.py tests/engine/test_gate_service.py tests/corpus/test_phase0.py"
rtk test "cd backend && .venv/bin/pytest -q tests/api/test_evidence_api.py tests/api/test_gates_api.py tests/api/test_m4_exit_flow.py tests/api/test_m5_exit_flow.py tests/api/test_drafting_api.py tests/api/test_package_api.py"
rtk test "cd backend && .venv/bin/pytest -q tests/package/test_manifest.py tests/package/test_build.py"
rtk make hub-check
rtk make verify
```

If frontend surfaces change, also run:

```bash
rtk proxy sh -lc 'cd frontend && npm run typecheck && npm run test'
```

## Acceptance Criteria

- No post-evidence late-document path leaves stale downstream work silently current.
- Registry bump behavior is centralized and tested against the invalidation matrix.
- Old derived records remain auditable but cannot be approved, packaged, or displayed as current.
- A crash after registry sync cannot permanently skip invalidation, and concurrent demand/package
  completion cannot restore a stale forward gate.
- Deployment reconciliation does not grandfather matters that were already stale before this fix.
- Package-ready artifacts remain immutable; new records require an explicit new cycle.
- An authorized user can explicitly start that new cycle without mutating historical artifacts.
- G2a freezes only after EX-token settlement, and package assembly cannot mutate the registry or
  publish an artifact set labeled with a registry version older than the matter's current version.
