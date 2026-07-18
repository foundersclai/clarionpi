# S5 — Publish immutable analysis and evidence generations

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-05
- Slice ID: S5
- Dependencies: S2, S3, S4
- Mergeability: ordered
- Deployment: safe
- Safe intermediate state: current pointers move only after a complete generation settles with its run.
- Final integration owner: S19

## Goal and non-goals

- Goal: bind analysis output, evidence versions, strategy-input revisions, and G2a results to exact runs.
- Observable success: partial, stale, failed, or aborted generations never become current authority.
- Non-goals: requested-demand elections, replay implementation, or workshop matter classification.
- Assumptions requiring confirmation: existing legacy rows can be tagged without inventing authorship.

## Live-code grounding

- Owner modules: brain1 analysis/risk, orchestrator Phase-0/analysis completion, evidence API, ORM.
- Existing seams: registry-version fence, evidence picks/dispositions, gate records, analysis SSE.
- Consumers: G2a view/action, plan emission, package manifest, late-document invalidation.
- Contracts: brain1 chronology/risk, orchestrator, API view models, tokenizer, and core run ownership.
- Compatibility: legacy evidence stays readable as history but cannot authorize a new action if unbound.

## Data flow and blast radius

Corpus/registry head → generation fence validator → analysis/evidence owner → G2a consumer → exact
gate result. Domain rows are installed under one successful generation pointer; drift or failure
seals partial rows non-current and terminalizes the owning run without false completion.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Analysis → exact evidence authority | corpus/registry head → generation fence → analysis owner → G2a gate → immutable evidence result | complete generation becomes current | stale head refuses | later human disposition does not rewrite generation hash | failed or expired generation stays noncurrent | no gate advance or partial current pointer | `happy → backend/tests/engine/test_analysis_generation.py::test_g2a_reads_only_pointed_current_generation_and_never_building_stale_or_max_id; negative → backend/tests/corpus/test_phase0_operation_concurrency.py::test_upload_commit_after_admission_fails_final_fence_without_gate_or_cursor_success_until_new_document_processed; edge → backend/tests/engine/test_analysis_generation.py::test_generation_content_hash_covers_generated_output_but_not_later_human_disposition; retry/terminal → backend/tests/engine/test_analysis_generation.py::test_failed_aborted_or_expired_analysis_stales_partial_and_never_moves_current_pointer; side effects → backend/tests/api/test_evidence_api.py::test_stale_evidence_mutation_or_g2a_echo_refuses_before_business_audit_gate_or_run_write` |

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

- Commands: focused analysis/evidence tests plus Postgres late-document races.
- Expected failures: current mutable/latest-row behavior can expose partial or stale outputs.
- Observed failures: add run/generation/head diagnostics before altering completion.
- Characterization exception: legacy provenance is tagged explicitly and cannot be upgraded in place.
- LLM integration omission: scripted analysis providers cover deterministic settlement.

## Implementation sequence

1. Add append-only strategy-input revisions and generation/evidence candidate keys with preflights.
2. Bind child rows and results to exact run, corpus, registry, and generation identities.
3. Publish generated narratives/risks/evidence through atomic current-pointer settlement.
4. Require G2a payloads and mutations to echo the exact current evidence triple.
5. Update contracts and run SQLite/Postgres drift, abort, and carry-forward tests.

## Verification and acceptance

- Only complete successful generations become current and every G2a result is exact-bound.
- Failed, stale, or aborted attempts retain truthful history without current authority.
- Upload races cannot publish stale analysis, evidence, audit, or gate success.
- `make verify` and named Postgres tests pass.

