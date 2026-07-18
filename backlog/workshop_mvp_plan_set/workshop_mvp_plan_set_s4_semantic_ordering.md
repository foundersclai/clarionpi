# S4 — Make shared semantic ordering deterministic

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-04
- Slice ID: S4
- Dependencies: S3
- Mergeability: ordered
- Deployment: safe
- Safe intermediate state: standard outputs use stable semantic keys without enabling workshop behavior.
- Final integration owner: S19

## Goal and non-goals

- Goal: remove UUID, insertion-order, and timestamp ties from derived rows and prompt inputs.
- Observable success: two fresh databases produce identical semantic and prompt-hash sequences.
- Non-goals: byte-identical XLSX output, replay catalog contents, or demo artifact overlays.
- Assumptions requiring confirmation: every shared collection has a domain-owned semantic key.

## Live-code grounding

- Owner modules: extraction merge, chronology/risk, tokenizer allocation, brain2 prompts, and package views.
- Existing consumers: analysis composition, drafter/judge prompts, provenance ordering, and artifact builders.
- Fallbacks: tagged legacy corpus order remains explicit and cannot claim verified arrival order.
- Contracts: corpus extraction, brain1 chronology/risk, tokenizer, brain2, compliance, and package.
- Compatibility: values and membership remain unchanged; only unstable presentation/call order changes.

## Data flow and blast radius

Persisted source identities → semantic-key validator → owning collection builder → prompt/package
consumer → stable hash/vector. Invalid or duplicate keys fail before provider calls or artifact
writes; shuffle, retry, and resume preserve the same ordered vector.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Persisted rows → semantic outputs | source identities → ordering validator → domain owner → prompt/package consumer → hash vector | fresh databases match | invalid key refuses | same-day and tied rows stay stable | shuffle/retry/resume preserves vector | no provider call or artifact on invalid order | `happy → backend/tests/workshop/test_deterministic_ordering.py::test_two_fresh_databases_have_identical_semantic_and_prompt_sequences; negative → backend/tests/workshop/test_deterministic_ordering.py::test_invalid_source_order_refuses_before_provider_or_artifact; edge → backend/tests/workshop/test_deterministic_ordering.py::test_same_day_chronology_and_narrative_order_is_semantic; retry/terminal → backend/tests/workshop/test_deterministic_ordering.py::test_shuffle_tie_retry_resume_preserves_order_and_call_vector; side effects → backend/tests/workshop/test_deterministic_ordering.py::test_unexpected_order_has_no_replay_fallback_or_business_write` |

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

- Commands: deterministic-ordering tests on shuffled fixtures and two disposable databases.
- Expected failures: current UUID/timestamp fallbacks alter derived order or prompt hashes.
- Observed failures: add semantic-key diagnostics before changing collection order.
- Characterization exception: identity-only hash fields are explicitly excluded by the source contract.
- LLM integration omission: scripted providers assert canonical prompt ordering.

## Implementation sequence

1. Inventory every shared collection and record its semantic key and tie policy.
2. Add body-free diagnostics containing keys and prompt hashes, never source text.
3. Replace unstable ORM/set/dict ordering in extraction, brain1, tokenizer, brain2, and package paths.
4. Reject malformed ordering inputs before consumers and preserve tagged legacy semantics.
5. Update affected module contracts and run shuffle/two-database regressions.

## Verification and acceptance

- Chronology, risks, anchors, allocations, prompts, and package provenance are stable.
- Invalid ordering produces no provider invocation, business write, or artifact.
- Standard output meaning and late-document invalidation remain unchanged.
- `make verify` passes.

