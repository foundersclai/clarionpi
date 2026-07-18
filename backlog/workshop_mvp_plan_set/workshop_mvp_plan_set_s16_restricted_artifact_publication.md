# S16 — Publish permanently restricted demo artifacts

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-16
- Slice ID: S16
- Dependencies: S5, S8, S9, S10, S12, S13, S14, S15, S18
- Mergeability: ordered
- Deployment: feature_gated
- Safe intermediate state: demo package build/list/download stays refused until policy, approval, and publication checks all pass.
- Final integration owner: S19

## Goal and non-goals

- Goal: build four unmistakably restricted artifacts through fenced reserve-stage-publish ownership.
- Observable success: immutable sets bind exact approved draft/G3/compliance/policy bytes and survive crash recovery.
- Non-goals: send-readiness, exact-byte attorney approval, host-renderer fallback, or mutable artifact prefixes.
- Assumptions requiring confirmation: the pinned offline render image and bundled fonts are cached.

## Live-code grounding

- Owner modules: package builder/storage, ArtifactSet ORM, package/provenance API, frontend package view.
- Existing seams: build preflight/cache reuse, latest draft selection, storage writes, artifact downloads.
- Consumers: workshop inspection UI, evidence run, verifier, package history, and source round trip.
- Contracts: package builder, API view models, matter-access policy, operation runs, and lifecycle.
- Compatibility: standard outputs match the declared post-shared-baseline mode and remain unmarked.

## Data flow and blast radius

Exact approved authority/policy → publication fence validator → package/storage owner → list/download
consumer → restricted immutable bytes. Unique set-owned keys prevent policy rebuild overwrite;
commit-ack loss and cancellation reconcile durable publication/run truth before cleanup or retry.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Approval/policy → artifact bytes | draft/G3/head/policy → authority/fence validator → publication owner → package/download API → restricted set | four artifacts publish atomically | invalid authority refuses | policy rebuild preserves old bytes | lost commit ack reconciles published tuple | no partial set overwrite storage read or success audit | `happy → backend/tests/workshop/test_demo_artifacts.py::test_reserve_stage_publish_links_publication_set_run_and_four_objects_atomically; negative → backend/tests/workshop/test_demo_artifacts.py::test_package_and_download_refuse_invalid_g3_authority_before_cache_or_storage; edge → backend/tests/workshop/test_demo_artifacts.py::test_policy_change_uses_new_set_prefix_and_preserves_prior_set_bytes; retry/terminal → backend/tests/workshop/test_demo_artifacts.py::test_lost_commit_ack_reconciles_durable_published_tuple_without_deleting_final_bytes; side effects → backend/tests/workshop/test_demo_artifacts.py::test_artifact_approval_integrity_mismatch_serves_no_bytes_or_success_audit` |

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

- Commands: package publication/recovery tests, artifact parsers, crop probes, and pinned renderer tests.
- Expected failures: current keys and G3 draft status cannot prove immutable exact publication authority.
- Observed failures: add publication/owner/fence/result diagnostics without filenames containing facts.
- Characterization exception: chronology baseline remains semantic if S10 cannot close byte identity.
- LLM integration omission: package assembly adds no model call.

## Implementation sequence

1. Accept `docs/adr/0017-artifact-publication.md` and add reservation/publication/set/run tenant-safe schema.
2. Reserve exact authority/policy under the matter lock and render outside the DB transaction.
3. Stage/promote through storage-owned fenced keys, then atomically publish set/run/audit truth.
4. Add cancellation, stale-owner, commit-ack, cleanup, integrity quarantine, and retry reconciliation.
5. Add permanent three-band labels, pinned offline visual verifier, download hashes, and UI copy.

## Verification and acceptance

- All four artifacts parse, contain no unresolved tokens, and retain warnings after defined crops.
- Concurrent/crashed builds expose one complete immutable set and never overwrite earlier bytes.
- Invalid profile, purpose, approval, policy, hash, or byte count refuses before serving success.
- `make verify` and named Postgres/publication tests pass.
