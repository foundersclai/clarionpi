# S2 — Establish durable operation ownership

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-02
- Slice ID: S2
- Dependencies: S1
- Mergeability: ordered
- Deployment: dormant
- Safe intermediate state: additive run records have no caller until later slices migrate each operation.
- Final integration owner: S19

## Goal and non-goals

- Goal: own idempotency, leases, fencing, terminal truth, and typed results for long operations.
- Observable success: one operation key has one durable owner and an immutable terminal outcome.
- Non-goals: replay catalog behavior, workshop UI, artifact publication, or automatic retries.
- Assumptions requiring confirmation: existing synchronous callers remain compatible until migrated.

## Live-code grounding

- Owner modules: `backend/app/core`, ORM/enums/schemas, and the operation-run API facade.
- Existing seams: metered LLM client, matter budget, audit writes, engine SSE entry points.
- Consumers: Phase 0, analysis, plan emit, drafting, correction, compliance, and package assembly.
- Contracts: core telemetry/budget and orchestrator/API module contracts.
- Compatibility: standard callers can adopt the typed start/status/result contract incrementally.

## Data flow and blast radius

Idempotency request → admission/fence validation → operation-run owner → domain worker → immutable
terminal result. Lease expiry aborts or requires an explicit fresh resume; commit-ack loss is
reconciled from durable truth before another provider call or business mutation.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Request → durable run | idempotency key → typed fence validator → operation-run service → domain worker → terminal result | success commits result once | invalid fence refuses pre-row | concurrent duplicate has one owner | lease expiry requires explicit resume | no provider call or business write on refusal | `happy → backend/tests/core/test_operation_runs.py::test_success_commits_business_state_and_terminal_run_atomically; negative → backend/tests/api/test_operation_runs.py::test_pre_reservation_refusal_has_no_run_row_or_run_id; edge → backend/tests/core/test_operation_run_concurrency.py::test_different_fresh_keys_create_one_active_owner_and_no_loser_call; retry/terminal → backend/tests/core/test_operation_runs.py::test_expired_running_lease_reconciles_to_aborted_without_auto_restart; side effects → backend/tests/core/test_operation_runs.py::test_run_record_contains_only_allowlisted_body_free_provenance` |

## Independent matrix-completeness review

The downstream per-slice consensus review fills this scaffold against the implementation base.

<!-- matrix-attestation:start -->
- Reviewer/context:
- Matrix-Completeness-Gate:
- Matrix-Deferred-Findings:
- Matrix-Review-Content-SHA256:
- Matrix-Review-Base-SHA:
- Matrix-Review-Worktree:
- Changed seams and fallback/legacy paths audited:
- Every populated axis → exact deterministic test mapping confirmed:
- Producer failure + consumer response pairs confirmed:
- Forbidden side-effect assertions confirmed:
- N/A axes and concrete reasons confirmed:
- Pre-implementation findings resolved and plan re-reviewed:
- Late-gap rule acknowledged:
<!-- matrix-attestation:end -->

## Red-test evidence before production code

- Commands: focused core operation-run tests, then Postgres concurrency markers.
- Expected failures: duplicate owners, stale epochs, and commit-ack loss expose current non-durable gaps.
- Observed failures: not run because implementation has not started.
- Characterization exception: none; diagnostics precede behavior changes for silent failure paths.
- LLM integration omission: scripted providers cover operation ownership deterministically.

## Implementation sequence

1. Accept `docs/adr/0015-durable-operation-generations.md` and add tenant-safe run/result schema plus migration preflights.
2. Add typed start, heartbeat, settle, abort, reconcile, and explicit-resume services.
3. Enforce legal transitions with ORM guards, SQLite triggers, and Postgres constraints/locks.
4. Add guarded body-free status view models without migrating domain workers yet.
5. Update core/API contracts and run SQLite plus Postgres concurrency tests.

## Verification and acceptance

- Exactly one active owner exists per concurrency group and stale epochs cannot commit.
- Terminal results are immutable and commit atomically with their business outcome.
- Refusals and indeterminate states expose no body content or false success.
- `make verify` and the named integration tests pass.
