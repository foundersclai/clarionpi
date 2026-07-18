# S3 — Own ingest identity and duplicate ordering

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-03
- Slice ID: S3
- Dependencies: S2
- Mergeability: ordered
- Deployment: safe
- Safe intermediate state: additive corpus identities and ordinals are consumed by the ordinary ingest path.
- Final integration owner: S19

## Goal and non-goals

- Goal: give every committed document stable matter-local order and every Phase-0 result an owned corpus head.
- Observable success: exact duplicates always select the lowest declared ingest ordinal.
- Non-goals: workshop manifest sealing, semantic ordering above corpus, or provider replay.
- Assumptions requiring confirmation: legacy documents receive an explicit backfill identity without arrival claims.

## Live-code grounding

- Owner modules: corpus upload sessions, dedup, Phase 0, CaseDocument ORM, and registry bump.
- Existing symbols: `UploadSlot.ordinal`, canonical sort in `dedup.py`, and Phase-0 completion owner.
- Consumers: extraction windows, registry versioning, analysis admission, provenance, and UI document order.
- Contracts: corpus ingest/extraction, tokenizer, and orchestrator invalidation contracts.
- Compatibility: late-document invalidation and existing standard uploads remain active.

## Data flow and blast radius

Declared slot order → commit allocation validator → matter ingest cursor → dedup/Phase-0 consumers →
owned corpus result. Concurrent commits allocate disjoint ranges; retries preserve the first durable
identity, and a corpus-head drift prevents stale Phase-0 completion.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Upload order → corpus head | slot ordinal → commit identity validator → matter cursor → dedup/Phase 0 → corpus version | contiguous order persists | cross-session identity is refused | timestamp and UUID ties use ordinal | retry preserves canonical and head | rollback leaves cursor, documents, links, and audit unchanged | `happy → backend/tests/corpus/test_upload_sessions.py::test_commit_allocates_contiguous_matter_ordinals_in_slot_order; negative → backend/tests/corpus/test_phase0_admission.py::test_admission_uses_persisted_identity_and_never_opens_blob_storage; edge → backend/tests/corpus/test_dedup.py::test_exact_duplicate_canonical_is_lowest_matter_ordinal; retry/terminal → backend/tests/corpus/test_dedup.py::test_retry_preserves_canonical_and_single_pending_decision; side effects → backend/tests/corpus/test_upload_sessions.py::test_ordinal_commit_failure_rolls_back_cursor_documents_links_session_and_audit` |

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

- Commands: focused upload/dedup/Phase-0 tests plus Postgres concurrent commit tests.
- Expected failures: shuffled same-transaction uploads currently allow UUID-based canonical selection.
- Observed failures: add secret-free ordinal diagnostics before applying the ordering fix.
- Characterization exception: legacy backfill labels are explicit rather than fabricated arrival order.
- LLM integration omission: scripted Phase-0 providers are sufficient.

## Implementation sequence

1. Add diagnostic output for tied canonical candidates and declared ordinals.
2. Add matter ingest cursor, document ordinal, corpus versions, and tenant-safe candidate keys.
3. Allocate contiguous ranges under the matter lock and bind Phase-0 membership/results.
4. Make dedup and pending-document selection use owned order with legacy tagging.
5. Update corpus/tokenizer/orchestrator contracts and run real Postgres races.

## Verification and acceptance

- Two concurrent sessions receive disjoint contiguous ranges and retries do not advance twice.
- Dedup decisions are stable under timestamp/UUID ties and same-name distinct bytes remain separate.
- Stale Phase-0 completion cannot advance gates or registry cursors.
- `make verify` and named Postgres tests pass.
