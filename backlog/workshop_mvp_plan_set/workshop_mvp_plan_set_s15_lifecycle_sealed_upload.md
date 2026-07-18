# S15 — Own sealed upload and workshop lifecycle

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-15
- Slice ID: S15
- Dependencies: S11, S12, S13, S14, S18
- Mergeability: ordered
- Deployment: feature_gated
- Safe intermediate state: preparation stages validated immutable generations and activation is an authenticated atomic pointer transition.
- Final integration owner: S19

## Goal and non-goals

- Goal: seal manifest-approved uploads and provide safe prepare/reset/checkpoint/supervisor/doctor ownership.
- Observable success: five stop/reset/start/doctor cycles require no manual state edit and never touch non-workshop data.
- Non-goals: arbitrary paths, attendee uploads, public bind, host renderer fallback, or automatic human gate actions.
- Assumptions requiring confirmation: the workshop laptop has the pinned images/toolchain cached before offline use.

## Live-code grounding

- Owner modules: corpus upload sessions/blob swap, workshop workspace/checkpoint/supervisor CLI, operator seed.
- Existing seams: slot ordinal/hash/size, staged blob swap, session expiry, session auth, database/storage roots.
- Consumers: Phase 0, evidence sessions, frontend launch, doctor, recovery, and final integration.
- Contracts: corpus ingest, core runtime/access, API auth, and workshop lifecycle.
- Compatibility: standard upload never accepts client-owned expected hashes or workshop seals.

## Data flow and blast radius

Active scenario manifest → slot/hash/seal validator → upload/lifecycle owner → Phase-0/runtime consumer
→ immutable workspace generation. Reset validates canonical roots and stops authenticated owned
processes before mutation; failures preserve the prior generation and truthful cleanup state.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Manifest/workspace → active generation | scenario seal → hash/path/owner validator → lifecycle supervisor → app/Phase 0 → active workspace | sealed upload activates once | hash or unsafe root refuses | concurrent reset/start serializes | crash journal reconciles prior or new generation | no nonworkshop delete foreign signal or partial active pointer | `happy → backend/tests/corpus/test_workshop_uploads.py::test_hash_match_promotes_once_and_commit_seals_scenario; negative → backend/tests/workshop/test_workspace_lifecycle.py::test_reset_refuses_before_mutation; edge → backend/tests/workshop/test_workspace_lifecycle.py::test_concurrent_start_and_reset_serialize_without_pointer_or_process_split_brain; retry/terminal → backend/tests/workshop/test_workspace_lifecycle.py::test_activation_journal_reconciles_crash_at_each_phase; side effects → backend/tests/workshop/test_supervisor.py::test_pid_reuse_or_foreign_port_owner_is_never_signaled` |

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

- Commands: upload/session/blob recovery, workspace lifecycle, supervisor, operator seed, and doctor tests.
- Expected failures: no current owner seals scenario hashes or authenticates process/workspace generations.
- Observed failures: add hash/count/owner/epoch diagnostics without paths containing secrets or content.
- Characterization exception: test-only constructed flows never become rehearsal evidence or checkpoints.
- LLM integration omission: replay is injected from S14; lifecycle tests use fixed catalogs.

## Implementation sequence

1. Add immutable workspace generations, authenticated pointers, boot attestations, and canonical roots.
2. Add scenario seal, server-owned slots, expected hash/size validation, and fenced blob attempts.
3. Add generated operator credential, session/audit atomicity, and exact loopback supervisor ownership.
4. Add safe prepare/reset/checkpoint/capture/restore/doctor protocols with bounded cleanup and journals.
5. Add repo-owned commands/runbook and run SQLite/Postgres concurrency and five-cycle tests.

## Verification and acceptance

- Only the active manifest creates/uploads the single demo matter and seal; retries are idempotent.
- Unsafe roots, credentials, PIDs, ports, images, or toolchains refuse before mutation.
- Crash/cancel/expiry recovery cannot delete committed bytes or strand an active owner.
- `make verify` and all named Postgres races pass.
