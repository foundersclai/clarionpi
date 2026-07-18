# S18 — Seal draft, finding, and exact G3 authority

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-24
- Slice ID: S18
- Dependencies: S2, S5, S7, S14
- Mergeability: ordered
- Deployment: safe
- Safe intermediate state: replay remains feature-gated until immutable draft/finding history and exact G3 settlement are installed.
- Final integration owner: S19

## Goal and non-goals

- Goal: make draft/finding history append-only and bind G3 to the exact current approved bytes.
- Observable success: package authority names one immutable draft version, compliance head, and G3 record.
- Non-goals: attorney approval of served bytes, demo watermarking, or package storage publication.
- Assumptions requiring confirmation: S14 exposes typed replay outcomes without enabling draft consumers early.

## Live-code grounding

- Owner modules: DemandDraft/ComplianceFinding ORM, brain2 generation, compliance lifecycle, orchestrator, drafting API.
- Existing defect: G3 has no draft-approval side effect while package buildability requires approved status.
- Consumers: correction, compliance panel, gate history, package manifest/reuse/build, and evidence export.
- Contracts: brain2, compliance, orchestrator, API view models, operation runs, and package builder.
- Compatibility: historical draft/finding versions remain immutable and never become current by numeric latest.

## Data flow and blast radius

Exact approved plan/replay result → draft/finding/head validator → immutable lifecycle owner → G3 gate
consumer → package-authority tuple. Draft, correction, and finding operations publish new versions;
G3 atomically stamps only the echoed current tuple and refuses stale or changed-then-reverted content.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Draft/finding history → G3 authority | approved plan and replay result → draft/head validator → immutable compliance owner → G3 action → package authority | exact current tuple approves atomically | stale plan draft or head refuses | later revision cannot rewrite prior head | idempotent replay returns original revision result | no package authority reuse render gate or audit on failure | `happy → backend/tests/engine/test_draft_history.py::test_g3_approval_atomically_stamps_exact_current_draft_and_compliance_head; negative → backend/tests/api/test_drafting_api.py::test_pre_edit_plan_identity_cannot_approve_post_edit_content; edge → backend/tests/engine/test_compliance_history.py::test_later_finding_revision_does_not_change_prior_approved_head; retry/terminal → backend/tests/api/test_drafting_api.py::test_same_key_finding_replay_after_later_revision_returns_original_stored_revision_head_and_open_count; side effects → backend/tests/package/test_exact_g3_authority.py::test_unapproved_or_head_drifted_draft_blocks_package_before_reuse_or_render` |

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

- Commands: draft/finding history, G3 gate, correction, API replay, and package-authority tests.
- Expected failures: current G3 advances without marking the exact draft approved and package stays unbuildable.
- Observed failures: add draft/version/head/gate diagnostics before changing side effects.
- Characterization exception: ADR-0009/WI-1 exact-served-byte attorney approval remains held.
- LLM integration omission: S14 scripted replay provides deterministic draft/compliance responses.

## Implementation sequence

1. Accept ADR-0016 and add immutable draft, finding-revision, head, run, and plan candidate keys.
2. Publish demand/correction/compliance results as append-only versions with exact owning runs.
3. Make retries return the original stored version/head and seal failed partials noncurrent.
4. Add atomic G3 side effect binding current approved draft/content/head and gate result.
5. Require package view/manifest/reuse/build to use the exact authority tuple; update contracts.

## Verification and acceptance

- Draft/finding correction never rewrites history and numeric latest is not authority.
- G3 success, draft approval, compliance head, gate result, audit, and run commit atomically.
- Stale or invalid tuples cannot make package buildable or reach cache/render/storage.
- `make verify` and named Postgres constraint tests pass.
