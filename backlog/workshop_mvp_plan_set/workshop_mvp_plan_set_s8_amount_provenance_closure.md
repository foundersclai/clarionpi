# S8 — Close amount provenance through package authority

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-08
- Slice ID: S8
- Dependencies: S6, S7
- Mergeability: ordered
- Deployment: safe
- Safe intermediate state: only complete typed provenance becomes current package authority.
- Final integration owner: S19

## Goal and non-goals

- Goal: make requested demand and medical specials resolve through distinct complete provenance paths.
- Observable success: package and API prove human election origin or all contributing source pages.
- Non-goals: artifact demo labels, legal valuation, or rewriting invalidated historical tokens.
- Assumptions requiring confirmation: S6 and S7 have installed v1 anchor and election equivalence.

## Live-code grounding

- Owner modules: tokenizer provenance/resolver, package manifest/build checks, provenance API/view models.
- Existing seams: token-to-anchor route, fact registry pins, package build preflight, DOCX renderer.
- Consumers: attorney inspection, package report, token resolution, artifact manifest, audit.
- Contracts: tokenizer provenance, API view models, brain2, and package builder.
- Compatibility: source-derived facts still require pages; human-origin demand explicitly has none.

## Data flow and blast radius

Typed amount token → provenance-shape validator → resolver/package owner → API/artifact consumer →
human election or ordered page sink. Legacy, mixed, unanchored non-empty, or stale-election tokens
refuse before allocation, package cache reuse, rendering, or successful audit.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Amount token → provenance sink | typed token origin → provenance validator → resolver owner → package/API consumer → election or bill pages | both roles resolve correctly | mixed or stale origin refuses | zero ledger is nonrenderable | corrected version supersedes without mutation | no package reuse render or success audit on invalid token | `happy → backend/tests/api/test_provenance_api.py::test_requested_demand_projects_human_election_and_no_page_claim; negative → backend/tests/engine/test_amount_provenance.py::test_mixed_amount_origin_shape_refuses_resolution; edge → backend/tests/engine/test_amount_provenance.py::test_ledger_empty_is_nonrenderable_and_never_required; retry/terminal → backend/tests/engine/test_amount_provenance.py::test_corrected_amount_version_preserves_invalidated_history; side effects → backend/tests/package/test_amount_provenance.py::test_invalid_amount_authority_refuses_reuse_render_and_success_audit` |

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

- Commands: focused tokenizer/provenance/package tests and artifact parser checks.
- Expected failures: current amount tokens cannot distinguish human election from source ledger origin.
- Observed failures: use S6/S7 diagnostics and add package refusal logging before closure changes.
- Characterization exception: zero-ledger and legacy history remain explicit nonrenderable states.
- LLM integration omission: provenance closure is deterministic.

## Implementation sequence

1. Extend registry equivalence and resolver dispatch over the closed amount-origin vocabulary.
2. Require exact election/version or complete ledger/anchor evidence at every consumer.
3. Update package preflight, cache keys, provenance view models, and report output.
4. Refuse invalid authority before storage/cache/render/audit and preserve historical pins.
5. Update contracts and run API/package artifact regressions.

## Verification and acceptance

- Requested demand reports immutable actor/election provenance without claiming a page source.
- Medical totals report every line and unique ordered page; empty/legacy shapes cannot render.
- Change-then-revert or stale pins never regain current package authority.
- `make verify` passes.

