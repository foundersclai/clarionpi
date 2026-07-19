# WD-2 — Wire the G3 approve side effect so the current draft becomes APPROVED

- Parent roadmap: `backlog/planned/workshop_demo_milestone.md` (demo-track slice WD-2); corresponds
  to plan-set slice S18 (thin sub-scope: exact G3 draft approval only).
- Slice ID: WD-2 (owning plan-set slice: S18, thin)
- Dependencies: none — independent of WD-1/WD-3/WD-4; base `main`.
- Mergeability: independent.
- Deployment: safe — completes an intended, unwired state-machine side effect; no schema, migration,
  route, response-key, or status-code change. It does add one exact `GuardRefused` code inside the
  existing `409 guard_failed` shape and therefore includes the required contract/ADR updates.
- Safe intermediate state: n/a — one small self-contained PR.
- Final integration owner: the WD-0 milestone acceptance (roadmap), not this slice.
<!-- sdlc-tier-assessment:start -->
## SDLC tier assessment
- SDLC-Tier: 2
- SDLC-Minimum-Tier: 2
- SDLC-Tier-Status: APPROVED
- SDLC-Tier-Assessor: Claude Opus 4.8 (Claude Code) — clarionpi, single-engine read-only grounding
- SDLC-Tier-Content-SHA256: fdee6888963b4da1846f7eb9f181ee85fdbc9d8534f38ec6fd4850587bb6c49a
- SDLC-Tier-Base-SHA: 789843db14daefc1a10b929da6e9ea7b29c7249b
- SDLC-Tier-Triggers: internal producer→consumer side-effect — G3 approve/override sets latest_draft.status=APPROVED (service.py _SIDE_EFFECTS); sole consumer = buildable FE hint (view_models.py:681); adds a defensive DraftMissing refusal inside the existing 409 guard_failed shape (new wire code, unreachable on the normal path) — pre-production wire-scope modifier holds this at Tier 2; side-effect-registry change requires ADR-0018 + orchestrator/view-models/system-contract updates (module Change rule); no unmodified Tier-3 trigger fires — build fences on gate_state not draft status (drafting.py:613), GateRecord authoritative
- SDLC-Tier-Approval: user-approved in thread
- SDLC-Tier-Approval-Rationale: recommended — bounded reversible internal denorm side effect; the draft.status=APPROVED write controls no downstream semantic decision (mirrors the plan.approved denorm), so no legal/evidentiary Tier-3 trigger fires
- SDLC-Tier-Degraded-Assurance: NONE
- SDLC-Tier-Revalidation: unchanged-tier (post-consensus; converged content adds a pre-production-demoted defensive wire refusal + required ADR/contract updates; Tier 2 still covers minimum and recommendation; base re-keyed to main 789843d)
<!-- sdlc-tier-assessment:end -->

## Verify-first result (why this slice exists, corrected)

Driving the real flow (the passing `test_m5_exit_full_demand_package`): the demand package **already
builds** end-to-end. `post_package_build` (`backend/app/api/routes/drafting.py:613`) fences only on
`gate_state == GateState.PACKAGE_ASSEMBLY`, NOT on draft status; G3 approve reaches that state. The
earlier "package unreachable" framing was wrong.

The real defect: `DraftStatus.APPROVED` is **defined and read once but never assigned** anywhere in
`backend/app`. The `(COMPLIANCE_REVIEW, G3_APPROVED)` transition (`machine.py:78`) runs no side
effect (`_SIDE_EFFECTS`, `service.py:486`, has no G3 entry), so the compliance-passed draft stays
`IN_COMPLIANCE`. Consequences: `buildable` (`view_models.py:681` = `draft.status == APPROVED`) is
**permanently False**, and the FE package-card permanently shows a misleading
"the draft is not approved yet — building will refuse" hint (`frontend/components/package-card.tsx:223`)
on a matter whose draft passed compliance and whose build succeeds — a UI-truth/credibility defect.

## Goal and non-goals

- Goal: at G3 approve, mark the current compliance-passed draft `APPROVED` (the `DraftStatus`
  terminal the design already documents), so `buildable` and the FE hint tell the truth and the
  draft carries its G3-approval status.
- Observable success: after a successful G3 approve, `latest_draft(...).status == APPROVED`;
  `buildable` is `True` in `package_assembly`; the package still builds; the misleading FE hint no
  longer shows.
- Non-goals:
  - No change to the build route's gate (it stays a `gate_state` fence; `buildable` stays a FE hint,
    never a build gate — the build must not start depending on draft status).
  - No `approved_by`/`approved_at` columns on `DemandDraft` (it has none; the GateRecord is the
    authoritative approval trail, exactly as for plan approval at `service.py:464`).
  - No schema/migration, no new route/response key/status, no FE production-code change, no
    LLM/provider call.
  - No change to the invalidation/registry-bump cascade or to `latest_draft`.

## Live-code grounding

- Owner surface: `backend/app/engine/orchestrator/service.py` — the `_SIDE_EFFECTS` map
  (`service.py:486-489`) and its in-transaction dispatch `side_effect(db, matter=matter, user=user)`
  after guards pass (`service.py:868-872`, design D4). Existing side effects `_settle_exhibits_then_freeze`
  and `_approve_plan_version` (`service.py:464-483`) set the pattern: signature `(db, *, matter, user)`,
  raise `GuardRefused` subclasses on refusal.
- Direct dispatch callers: `GateAction.APPROVE` and `GateAction.OVERRIDE` share the same event lookup
  and `_SIDE_EFFECTS` dispatch (`service.py:859-873`), so both G3 actions receive the new status write;
  a successful transitioned approve retry still hits the pinned gate-state check before replay and
  never re-executes a side effect (`service.py:803-827`).
- Transition + guards: `(COMPLIANCE_REVIEW, G3_APPROVED) → PACKAGE_ASSEMBLY` with guards
  `("role_attorney", "registry_version_match", "no_blocking_findings")` (`machine.py:78-81`). Note the
  `no_blocking_findings` guard does NOT by itself guarantee a current draft: it counts open blocking
  findings on `latest_draft` and treats *no draft* as zero (`service.py:335-338`, `guards.py:140`), so
  it passes when no draft exists. A current, non-superseded draft is instead guaranteed on the normal
  flow by `DRAFTING`→generation plus the atomic registry-bump supersession/back-edge; the
  `registry_version_match` guard itself only compares the frozen and current registry versions and
  does not prove draft presence. The side effect therefore keeps an exact fail-loud no-draft branch.
- Current-draft selector: `latest_draft(db, *, matter)` (`backend/app/engine/compliance/engine.py:471`)
  — the single rule (highest version unless superseded, else `None`). `service.py:307` already
  imports it locally (`from app.engine.compliance.engine import latest_draft`), so `_approve_draft`
  uses the same local import — no orchestrator↔compliance cycle.
- Draft model: `DemandDraft` (`backend/app/models/orm.py:680-705`) has `status` (already a column)
  and NO approval-actor fields. `DraftStatus.APPROVED` is a valid enum member
  (`backend/app/models/enums.py:352`, docstring: "APPROVED at G3 approve (zero open blocking
  findings)").
- Status/current-draft consumer sweep: only `package_vm.buildable` branches on
  `status == APPROVED` (`view_models.py:681`). `build_guard_context` reads before the mutation;
  `compliance_review_vm` only serializes the pre-G3 status; `artifact_sets_view.current`, Brain-2's
  draft selector, the drafting/package route selector and completion fence, and the registry-bump
  scans distinguish only `SUPERSEDED` and therefore treat APPROVED like the prior current status.
  `latest_draft` has no additional re-export/caller contract. The package-card is the sole changed FE
  sink (`package-card.tsx:223-228`); its button remains enabled regardless of the hint.
- Cascade: `_supersede_stale_drafts` (`backend/app/engine/orchestrator/registry_bump.py:79-92`)
  supersedes every non-superseded older draft on a registry bump; an APPROVED draft is superseded
  exactly like `IN_COMPLIANCE`/`VALIDATED` (status `!= SUPERSEDED`). The
  `(PACKAGE_ASSEMBLY, REGISTRY_BUMPED) → EVIDENCE_REVIEW` edge (`machine.py:96`) already handles the
  stale-after-approve case.
- Affected existing callers/tests: `test_g3_approve_blocked_then_allowed`,
  `test_g3_approve_blocked_then_passes_after_disposition`, and
  `test_m5_exit_full_demand_package` are the three successful G3 paths to augment. The
  `IN_COMPLIANCE` assertions before G3 remain correct. Existing
  `test_bump_invalidates_all_stale_plans_and_supersedes_drafts` already seeds an approved draft and
  proves generic supersession; no test pins `_SIDE_EFFECTS` to a fixed size.
- Contract boundary: `docs/module_contracts/app.engine.orchestrator.md` explicitly classifies a
  side-effect-registry change as an ADR + module/system-contract change. The exact `draft_missing`
  wire instance also belongs in `docs/module_contracts/app.api.view_models.md`; affected system
  invariants are §1 (gated flow) and §9 (atomic audited sign-off). Sections §4/8/12 are unchanged
  because no deadline, role, or budget behavior moves.

## Mechanism and the design decision

First add a non-PHI fail-visible diagnostic at the dispatch seam, then add one exact refusal, one
side-effect function, and its registration:

```
class DraftMissing(GuardRefused):
    def __init__(self) -> None:
        super().__init__(
            guard="demand_draft",
            code="draft_missing",
            detail="no current DemandDraft for G3 approval",
        )

def _approve_draft(db, *, matter, user) -> None:
    from app.engine.compliance.engine import latest_draft   # local: mirrors service.py:307
    draft = latest_draft(db, matter=matter)
    if draft is None:
        raise DraftMissing()            # exact 409 guard_failed contract at the route
    draft.status = DraftStatus.APPROVED.value
    db.add(draft)

_SIDE_EFFECTS = { ...existing two..., (COMPLIANCE_REVIEW, G3_APPROVED): _approve_draft }
```

- Before registering the fix, add a `clarionpi.orchestrator` ERROR when the successful G3 dispatch
  finds no registered side effect (matter id/state/event only; no client facts). Run the focused G3
  red test and capture that log proving the missing map entry is the cause. Retain the diagnostic and
  its fallback test so a future wiring regression fails visibly.
- The `None` branch is defensive: a current draft is guaranteed by the flow (`COMPLIANCE_REVIEW` is
  only reachable post-`DRAFTING`, and registry-bump invalidation supersedes/back-edges atomically) —
  NOT by `no_blocking_findings`, which counts *no draft* as zero. `DraftMissing` is exported from
  `service.py`; the existing route mapping returns `409 {error: guard_failed, guard: demand_draft,
  code: draft_missing, detail: ...}` without a new status/shape branch.
- The GateRecord remains the authoritative approval trail; `draft.status = APPROVED` is the draft-row
  denorm of that fact (symmetric with `_approve_plan_version`'s `plan.approved` denorm).
- Design note: this completes the intended `DraftStatus` lifecycle in code rather than reinterpreting
  `buildable`. It deliberately does NOT make the build route depend on draft status — build
  reachability stays exactly as verified (the `gate_state` fence), so the slice is additive.
- Record the boundary decision in `docs/adr/0018-g3-draft-status-side-effect.md` (ADR 0013–0017 are
  reserved by the S1 charter plan-set, so WD-2 takes the next free number), update the
  orchestrator and API module contracts, and update `docs/system_contract.md` §§1 and 9. The ADR
  states that status is a denorm, `GateRecord` is authoritative, the write is transaction-local, and
  build authorization remains gate-state-only.

## Data flow and blast radius

G3 approve (attorney) → guards pass (role, registry match, no blocking findings) → `_approve_draft`
sets `latest_draft(...).status = APPROVED` in-transaction → state → `PACKAGE_ASSEMBLY` →
`buildable` reads `True`; the FE hint hides; `POST /package/build` works as before.

- Atomic: a refused G3 approve rolls the whole action back → draft stays `IN_COMPLIANCE`.
- Retry semantics unchanged: a duplicate successful G3 approve addressed to `compliance_review`
  hits `GateStateMismatch` before replay lookup, writes no second record, and does not re-run the
  side effect; the already-approved draft remains APPROVED. Direct repeated assignment is harmless
  but is not the service's replay behavior.
- Cascade-safe: a later registry bump supersedes the APPROVED draft (existing behavior) →
  `latest_draft` returns `None` → `buildable` False → machine cascades to `EVIDENCE_REVIEW`.
- Forward-only: matters already at/after `PACKAGE_ASSEMBLY` from before merge keep `IN_COMPLIANCE`
  drafts (no backfill); pre-production has no durable matters.

## Boundary and adversarial test matrix

| ID | Surface/path | Source → validator → owner → consumer → sink | Happy | Negative | Edge | Terminal/failure | Side effects present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | `_approve_draft` G3 side effect + refusal | G3 APPROVE/OVERRIDE → guards → `_SIDE_EFFECTS` dispatch → `_approve_draft` → `latest_draft.status=APPROVED` → state/record/audit commit | APPROVE and reasoned OVERRIDE on a compliance-passed matter both set only the current draft APPROVED and move to `package_assembly` | exact map entry exists; missing-entry fallback logs ERROR; role/registry/open-blocking refusals happen before dispatch and leave draft/state/record/audit unchanged; OVERRIDE cannot bypass an open blocking finding | no current draft after guards → `DraftMissing(demand_draft,draft_missing)` → exact 409 consumer body, no state/record/audit | an injected audit failure after the status mutation rolls the draft, gate, GateRecord, and audit back together | success has exactly one GateRecord + gate-action audit; no `LlmCall` or `ArtifactSet`; failure has neither record nor audit; no approval-actor fields | success/APPROVE+OVERRIDE → `backend/tests/engine/test_gate_service.py::test_g3_action_marks_only_current_draft_approved` (parameterized by the two actions; assert record/audit present and `LlmCall`/`ArtifactSet` absent); resolved-blocker integration → `backend/tests/engine/test_compliance_engine.py::test_g3_approve_blocked_then_allowed` (add refused-leaves-draft-unapproved [its seeded `validated` status is unchanged] then allowed-APPROVED assertions) and `backend/tests/api/test_drafting_api.py::test_g3_approve_blocked_then_passes_after_disposition` (same wire flow); registration/diagnostic → `backend/tests/engine/test_gate_service.py::test_side_effects_map_registers_g3_draft_approval`, `::test_missing_g3_side_effect_logs_diagnostic`; pre-dispatch refusals → `::test_g3_guard_refusals_leave_draft_and_sinks_untouched` (parameterized role/registry/open-blocking) + `::test_g3_override_cannot_bypass_open_blocking_finding`; no-draft service/wire → `::test_g3_draft_missing_refuses_atomically`, `backend/tests/api/test_drafting_api.py::test_g3_approve_without_current_draft_returns_exact_guard_failure`; post-write rollback → `backend/tests/engine/test_gate_service.py::test_g3_audit_failure_rolls_back_draft_status_gate_and_records` |
| BM-02 | `buildable` and FE hint | approved status → `package_vm` → gate envelope → `PackageCard` hint | after G3, `package_assembly` VM has `buildable=True`; hint absent | an APPROVED draft outside `package_assembly` has `buildable=False`; the false hint is present but the build button remains enabled (hint only) | `package_assembly` with a superseded highest draft → `latest_draft=None` → `buildable=False` | N/A — this is a read model; refusal/atomicity belongs to BM-01 | no response shape or FE production-code change; normal successful G3 emits no missing-side-effect diagnostic | positive + diagnostic absence → `backend/tests/api/test_drafting_api.py::test_package_vm_buildable_true_after_g3_approve`; full flow → `backend/tests/api/test_m5_exit_flow.py::test_m5_exit_full_demand_package` (assert APPROVED + `buildable=True` before build); non-assembly → `backend/tests/api/test_drafting_api.py::test_package_vm_buildable_false_outside_package_assembly`; superseded fallback → `::test_package_vm_buildable_false_with_superseded_current_draft`; FE sink → `frontend/__tests__/components/package-card.test.tsx` test title `PackageCard — package_assembly build > shows the not-buildable hint only when buildable is false` (paired true/false renders; false still exposes enabled build button) |
| BM-03 | Registry-bump cascade | APPROVED older draft + bump → `_supersede_stale_drafts` → SUPERSEDED → `latest_draft=None` + PACKAGE_ASSEMBLY back-edge | APPROVED participates in the existing non-superseded query and is preserved historically as SUPERSEDED | N/A — status-independent query; generic non-superseded coverage already exists | package-assembly bump produces SUPERSEDED, `latest_draft=None`, `buildable=False`, `evidence_review` on the same matter | bump rollback/retry contract is unchanged and therefore excluded (no changed failure behavior) | no change to `_supersede_stale_drafts`, `latest_draft`, the bump caller, or audit vocabulary | generic supersession → existing `backend/tests/engine/test_registry_bump.py::test_bump_invalidates_all_stale_plans_and_supersedes_drafts` (make APPROVED input explicit); complete changed consumer outcome → new `::test_approved_draft_registry_bump_cascades_package_assembly_to_evidence_review` (assert all four edge results in one test) |
| BM-04 | Forbidden effects: build authorization, replay, schema | unchanged package route + pinned gate-service ordering + diff scope | package build still succeeds from `package_assembly` even with an `IN_COMPLIANCE` current draft (proves status was not added as authorization) | duplicate successful G3 addressed to old gate gets GateStateMismatch before replay, one GateRecord only, approved status unchanged | N/A — no schema branch; existing `DraftStatus.APPROVED` column/enum are reused | N/A — BM-01 owns new failure/rollback behavior | no LLM/provider ledger row, no ArtifactSet at G3 (BM-01 explicit assertions); no migration/model fields; no FE production diff | build-gate absence → `backend/tests/api/test_package_api.py::test_build_succeeds_from_package_assembly_with_in_compliance_draft`; retry ordering → `backend/tests/engine/test_gate_service.py::test_duplicate_successful_g3_approve_mismatches_before_replay`; no-migration/no-fields/FE-production-change → focused diff-scope verification (not a standing repository-inventory test) |

Notes on allocation:
- Outcomes are grouped by behavior, not enum value: the three pre-dispatch guard refusals share one
  parameterized sink-absence test; APPROVE/OVERRIDE share one parameterized successful-dispatch test.
- Failure contracts introduced here are paired at service and HTTP consumers; the separate
  post-side-effect audit failure proves the newly-mutated draft participates in the existing atomic
  rollback contract. Distinct forbidden sinks have explicit assertions: `LlmCall`, `ArtifactSet`,
  GateRecord, AuditEvent, and the build route's status-agnostic authorization.
- Unchanged direct consumers are excluded with evidence: compliance VM/status serialization,
  artifact currentness, Brain-2, package build selection/completion, registry-bump caller/retry, and
  the `latest_draft` export all treat APPROVED as non-SUPERSEDED. Existing approved draft fixtures
  remain valid; no fixture contract changes.
- BM-02 terminal/failure is N/A because a pure view-model read neither commits nor refuses. BM-03
  negative/failure is N/A because the unchanged status-independent query and bump transaction already
  cover those outcomes. BM-04 schema/terminal is N/A because no schema path exists; diff-scope review
  is the precise check and avoids a future-failing migration inventory test.

## Independent matrix-completeness review

Scaffold — filled by the fresh-context attestation at the end of `plan-consensus-loop`.

<!-- matrix-attestation:start -->
- Reviewer/context: fresh-context read-only Claude (Opus 4.8, Claude Code) — independent attestor; did not draft, correct, or edit the plan; grounded live source under backend/app + frontend
- Matrix-Completeness-Gate: PASS
- Matrix-Deferred-Findings: NONE
- Matrix-Review-Content-SHA256: 771441778a4c3e8bafc78160ab4f3c7ea02a28b4891cafbf0d30b90e7d4db970
- Matrix-Review-Base-SHA: 789843db14daefc1a10b929da6e9ea7b29c7249b
- Matrix-Review-Worktree: clean-except-plan
- Confirmed seams/axes: BM-01 (_approve_draft + DraftMissing refusal + _SIDE_EFFECTS registration + dispatch diagnostic), BM-02 (buildable/FE hint), BM-03 (registry-bump supersession cascade), BM-04 (forbidden build-authorization/replay/schema) — each populated happy/negative/edge/terminal/side-effect axis maps to an exact deterministic test id
- Producer/consumer pairing: DraftMissing → generic GuardRefused→409 guard_failed mapping (routes/gates.py:164-182), paired at service + HTTP consumers
- Forbidden-side-effect assertions: LlmCall / ArtifactSet / second GateRecord / false-completion asserted absent; no migration/model-field/FE-production change verified via diff-scope
- N/A justifications: concrete for BM-02 terminal (pure read), BM-03 negative (status-independent query), BM-04 schema/terminal (no schema path)
- Late-gap findings: NONE outstanding — two mapping-class test-augment nits (a red-evidence test-id mismatch and a validated-fixture mislabel) were repaired before this attestation and re-cleared by both engines and this fresh attestor
<!-- matrix-attestation:end -->

## Diagnostic and red-test evidence before production code

- Diagnostic first: add the missing-G3-side-effect ERROR log and its fallback test before adding
  `_approve_draft` or the map entry. Then run
  `cd backend && .venv/bin/pytest -q -o log_cli=true --log-cli-level=ERROR tests/api/test_drafting_api.py::test_package_vm_buildable_true_after_g3_approve`.
  Capture the `clarionpi.orchestrator` log showing the successful G3 dispatch had no registered side
  effect; only then apply the code fix. The same final test asserts the diagnostic is absent on the
  repaired normal path.
- Backend red command: `cd backend && .venv/bin/pytest -q tests/engine/test_gate_service.py tests/engine/test_compliance_engine.py tests/engine/test_registry_bump.py tests/api/test_m5_exit_flow.py tests/api/test_drafting_api.py tests/api/test_package_api.py`.
- Frontend focused command: `cd frontend && npm run test -- --run __tests__/components/package-card.test.tsx`.
- Expected failures before code: the new BM-01 tests fail because `_SIDE_EFFECTS` has no
  `(COMPLIANCE_REVIEW, G3_APPROVED)` entry, so a G3 approve leaves the draft `IN_COMPLIANCE`
  (`test_g3_action_marks_only_current_draft_approved`, `test_side_effects_map_registers_g3_draft_approval`);
  the added `test_g3_approve_blocked_then_allowed` draft-APPROVED assertion fails; the BM-02
  `buildable` tests fail (buildable is False at `package_assembly` today). The exact no-draft,
  rollback, build-gate characterization, cascade, and FE true/false tests pass before the fix.
- Characterization: the current `test_m5_exit_full_demand_package` proves the package builds today
  with an unapproved draft — capture that as the before-state, then add the APPROVED/buildable
  assertions.
- Observed failures: not run yet (implementation not started).
- LLM integration omission: no model surface is touched.

## Implementation sequence

1. Add the dispatch diagnostic + fallback test, run the exact diagnostic command, capture the
   missing-entry log, and run `make test` before changing behavior.
2. Add the BM-01–BM-04 tests/assertions and capture the backend red command; run the focused FE test
   to lock the hint sink without changing FE production code.
3. Add/export `DraftMissing`, add `_approve_draft` (local `latest_draft` import), and register
   `(COMPLIANCE_REVIEW, G3_APPROVED): _approve_draft` in `_SIDE_EFFECTS`.
4. Add `docs/adr/0018-g3-draft-status-side-effect.md`; update
   `docs/module_contracts/app.engine.orchestrator.md`,
   `docs/module_contracts/app.api.view_models.md`, and `docs/system_contract.md` §§1/9. Record §4/8/12
   as unaffected rather than changing their behavior.
5. Run the focused backend and frontend commands, `make test` after the code change, then
   `make lint`, `make typecheck`, and `make verify` (which includes hub/contract and full frontend
   gates).

## Verification and acceptance

- All BM-01–BM-04 tests and the two augmented existing tests pass; `make verify` green.
- After a successful G3 approve, `latest_draft(...).status == APPROVED`, `buildable` is `True` at
  `package_assembly`, and the package still builds; a refused approve leaves the draft
  `IN_COMPLIANCE`.
- `DraftMissing` surfaces the exact existing-shape 409 and leaves state/records/audit untouched; an
  audit failure after the status write rolls that write back; successful G3 creates no LLM-call or
  artifact row; the diagnostic is silent on the normal repaired path.
- The build route, `latest_draft`, `_supersede_stale_drafts`, the `DemandDraft` schema, all routes,
  and FE production code are unchanged; no alembic version added. The ADR and affected module/system
  contracts describe the new side effect and refusal exactly.
