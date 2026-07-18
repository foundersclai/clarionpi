# S14 — Add strict canonical deterministic replay

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-14
- Slice ID: S14
- Dependencies: S2, S3, S4, S5, S6, S7, S8, S9, S11, S12, S13
- Mergeability: ordered
- Deployment: feature_gated
- Safe intermediate state: replay is constructible only inside validated workshop composition and has no network fallback.
- Final integration owner: S19

## Goal and non-goals

- Goal: replay exact canonical prompt identities while settling durable invocation, call, budget, and run truth.
- Observable success: the frozen stage/hash vector yields typed responses with zero network access and exact counts.
- Non-goals: live-provider benchmarking, prose normalization, FIFO fixtures, or package model calls.
- Assumptions requiring confirmation: the completed shared baseline freezes every prompt-producing semantic input.

## Live-code grounding

- Owner modules: core provider/telemetry/budget, new replay canonicalizer/catalog/provider, engine boundaries.
- Existing consumers: classification, extraction, analysis narratives, plan, drafting, compliance, correction.
- Failure seams: catalog miss, response validation, telemetry failure, timeout, disconnect, and retry.
- Contracts: core telemetry/budget/runtime/run-events plus every engine provider boundary.
- Compatibility: standard/live provider behavior remains behind the same metered client.

## Data flow and blast radius

Canonical prompt/stage/model → catalog/schema validator → metered replay invocation owner → engine
consumer → typed durable result. Token aliases are normalized structurally and rehydrated exactly;
any miss or telemetry failure terminalizes honestly with no network fallback or business success.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Canonical prompt → replay settlement | stage/model/prompt → canonical catalog validator → metered replay owner → engine boundary → typed result | exact key returns response | catalog miss is terminal | token ID changes rehydrate | retry is identical with new attempt identity | no network fallback body persistence or false completion | `happy → backend/tests/workshop/test_replay_provider.py::test_same_key_returns_identical_non_fifo_response; negative → backend/tests/workshop/test_replay_provider.py::test_catalog_miss_never_falls_back_or_constructs_network_provider; edge → backend/tests/workshop/test_replay_provider.py::test_runtime_token_id_change_rehydrates_without_prose_normalization; retry/terminal → backend/tests/workshop/test_replay_provider.py::test_retry_returns_same_response_and_attempt_identity_changes; side effects → backend/tests/workshop/test_replay_telemetry.py::test_telemetry_never_persists_prompt_or_response_body` |

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

- Commands: replay canonicalizer/catalog/provider/telemetry tests and engine boundary SSE tests.
- Expected failures: current provider factory has only null/Anthropic and environment selection bypasses Settings.
- Observed failures: record only stage, model, hashes, attempt IDs, and result codes.
- Characterization exception: replay proves the sealed scenario path, not general model quality.
- LLM integration omission: the replay catalog is the deterministic integration surface.

## Implementation sequence

1. Accept the replay/draft-history ADR dependencies and freeze the shared prompt baseline.
2. Add strict canonicalization, catalog schema/signature, token aliasing, and response validation.
3. Add replay behind the metered client with ProviderInvocation/LlmCall/budget/run atomic settlement.
4. Thread typed domain events/errors through REST/SSE without engine-owned wire formatting.
5. Freeze the exact call vector and update core/engine/API contracts and recovery tests.

## Verification and acceptance

- Every invocation has one exact attempt/call/budget/run tuple and zero persisted bodies.
- Misses, malformed responses, telemetry faults, and disconnects never emit false completion.
- Workshop construction cannot instantiate network providers and standard cannot select replay.
- `make verify` and named Postgres settlement races pass.

