# S13 — Persist demo identity and evidence authority

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-13
- Slice ID: S13
- Dependencies: S5, S11
- Mergeability: ordered
- Deployment: feature_gated
- Safe intermediate state: demo creation remains unavailable while all persisted demo content is policy-refused outside workshop.
- Final integration owner: S19

## Goal and non-goals

- Goal: stamp immutable matter identity, guard every content capability, and own the workshop evidence session.
- Observable success: wrong profiles refuse content and every interactive write binds one running evidence session.
- Non-goals: replay responses, scenario generation, artifact overlay bytes, or client-selected demo identity.
- Assumptions requiring confirmation: route/service callers can resolve minimal tenant-scoped matter identity first.

## Live-code grounding

- Owner modules: Matter/evidence ORM and migrations, core matter-access policy, matter/evidence APIs, view models.
- Existing consumers: corpus, engine gates, providers, package builder, provenance, lifecycle, and frontend controls.
- Invariants: every firm-scoped row retains firm ID and every indirect identifier resolves its parent safely.
- Contracts: new matter-access contract plus API, corpus, engine, package, and storage callers.
- Compatibility: standard list/get may expose only an approved restricted tombstone shape for demo rows.

## Data flow and blast radius

Server-owned scenario identity → immutable purpose validator → matter-access policy → named capability
consumer → data/workflow sink. A separate running evidence-session fence binds every interactive
upload, gate, operation, invocation, and artifact. Wrong profile, mismatched identity, absent session,
or finish races refuse before content reads, workflow writes, cache, render, storage, or export.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Persisted purpose → capability | server-stamped identity → tenant/purpose validator → matter-access policy → route/service → content sink | matching workshop capability passes | wrong profile or tenant refuses | indirect ID resolves minimal parent | retry repeats same refusal | no provider storage cache workflow or success audit | `happy → backend/tests/core/test_demo_purpose_policy.py::test_matching_profile_and_purpose_grants_named_capability; negative → backend/tests/core/test_demo_purpose_policy.py::test_wrong_profile_refuses_demo_service_before_sink; edge → backend/tests/api/test_demo_purpose_policy.py::test_indirect_identifier_resolves_minimal_parent_before_refusal; retry/terminal → backend/tests/core/test_demo_purpose_policy.py::test_policy_retry_repeats_same_refusal_without_state_change; side effects → backend/tests/core/test_demo_purpose_policy.py::test_demo_refusal_precedes_pack_authority_cache_and_storage` |
| BM-02 | Evidence session → interactive authority | workspace/matter identity → running-session validator → evidence service → workflow writer/export → immutable evidence record | session starts finishes and exports | client-owned provenance refuses | finish versus writer has one winner | commit or export ack loss reconciles | no post-snapshot write or fabricated terminal record | `happy → backend/tests/api/test_workshop_evidence_runs.py::test_start_current_get_finish_and_export_match_frozen_methods_paths_dtos_and_statuses; negative → backend/tests/api/test_workshop_evidence_runs.py::test_wire_refuses_extra_fields_csrf_wrong_profile_cross_tenant_and_client_owned_provenance_before_row_or_export; edge → backend/tests/workshop/test_evidence_run_concurrency.py::test_finish_vs_workflow_writer_has_one_locked_winner_and_no_postsnapshot_link; retry/terminal → backend/tests/api/test_workshop_evidence_runs.py::test_start_and_finish_idempotency_commit_ack_loss_and_export_pending_recover_from_durable_truth; side effects → backend/tests/workshop/test_evidence_run.py::test_finish_refuses_live_upload_operation_gate_or_publication_without_snapshot_or_state_change` |

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

- Commands: policy service/API tests, direct-SQL constraints, and cross-tenant route parameterization.
- Expected failures: process environment alone cannot preserve or enforce demo classification after restart.
- Observed failures: add purpose/capability/result-code diagnostics with no matter facts.
- Characterization exception: standard tombstones reveal only a closed non-content allowlist.
- LLM integration omission: sinks are spies proving refusal order.

## Implementation sequence

1. Add immutable purpose/scenario/version/label fields and `WorkshopEvidenceRun` candidate keys.
2. Add one typed `MatterAccessContext` policy service and forbid lower-layer bypass imports.
3. Add closed start/current/finish/export services with server-derived counts, links, and hashes.
4. Thread purpose capabilities and running evidence identity through every interactive consumer.
5. Project authoritative disclosure/run controls/tombstones and update all affected contracts.

## Verification and acceptance

- Client payloads cannot set, mutate, omit, or launder demo identity.
- Wrong-profile and cross-tenant paths refuse before every content-bearing sink.
- Evidence finish locks out concurrent writers and only exact completed records can export/checkpoint.
- Demo identity, labels, and evidence links survive restart, movement, and artifact history.
- `make verify` plus SQLite/Postgres constraint tests pass.
