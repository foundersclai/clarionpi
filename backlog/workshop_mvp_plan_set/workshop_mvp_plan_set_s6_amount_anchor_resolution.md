# S6 — Resolve medical amounts to bill-page anchors

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-06
- Slice ID: S6
- Dependencies: S3, S4, S5
- Mergeability: ordered
- Deployment: safe
- Safe intermediate state: newly minted medical tokens gain anchors while preserved legacy rows remain nonauthoritative.
- Final integration owner: S19

## Goal and non-goals

- Goal: derive ordered unique page anchors from every contributing billing line before amount mint.
- Observable success: a specials total resolves to all contributing line IDs and unique bill pages.
- Non-goals: requested-demand elections, amount semantic roles, or artifact watermarking.
- Assumptions requiring confirmation: each included BillingLine retains its required validated page anchor.

## Live-code grounding

- Owner modules: `backend/app/money`, tokenizer registry, BillingLine/FactToken models, provenance API.
- Existing symbols: `amounts_for_registry`, `mint_amounts`, `ledger_ref.line_ids`, and page anchors.
- Consumers: allocation, token resolution, provenance report, package checks, and source-page round trip.
- Contracts: money ledger, tokenizer, API provenance, and package builder.
- Compatibility: historical unanchored tokens remain explicit and cannot be silently rewritten.

## Data flow and blast radius

Included billing lines → tenant-scoped anchor resolver → money/token owner → provenance/package
consumer → ordered unique pages. Missing, cross-matter, excluded, or hash-drifted lines refuse
before a v1 amount token or package-authority write.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Ledger lines → amount provenance | billing line IDs → anchor/hash validator → tokenizer amount owner → provenance consumer → bill pages | total resolves all pages | missing or cross-matter line refuses | duplicate pages dedupe in source order | remint creates new version without rewriting history | no allocatable token on incomplete anchors | `happy → backend/tests/api/test_provenance_api.py::test_specials_returns_all_line_ids_and_two_unique_bill_pages; negative → backend/tests/engine/test_amount_anchors.py::test_missing_or_cross_matter_line_refuses_amount_mint; edge → backend/tests/workshop/test_deterministic_ordering.py::test_amount_anchor_order_is_document_ordinal_then_page; retry/terminal → backend/tests/engine/test_amount_anchors.py::test_remint_preserves_legacy_and_creates_anchored_successor; side effects → backend/tests/engine/test_amount_anchors.py::test_partial_anchor_resolution_writes_no_allocatable_token` |

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

- Commands: amount-anchor unit/API tests and focused package provenance checks.
- Expected failures: current `mint_amounts` emits empty anchors despite anchored ledger lines.
- Observed failures: first add amount/line/resolved-count diagnostics without source content.
- Characterization exception: preserved legacy unanchored history is intentionally nonauthoritative.
- LLM integration omission: this path is deterministic and model-free.

## Implementation sequence

1. Add diagnostics and a regression proving current no-anchor specials output.
2. Add one money-owned tenant-scoped line-to-anchor resolver with ledger-hash validation.
3. Mint ordered unique anchors and refuse incomplete non-empty medical totals.
4. Project all line IDs and pages through API/package provenance without duplicate claims.
5. Update money/tokenizer/API/package contracts and run focused tests.

## Verification and acceptance

- Non-empty medical totals have at least one valid page and retain all contributing line IDs.
- Anchor order is deterministic and cross-tenant or partial inputs write nothing authoritative.
- Historical unanchored rows are never upgraded in place or newly allocated.
- `make verify` passes.
