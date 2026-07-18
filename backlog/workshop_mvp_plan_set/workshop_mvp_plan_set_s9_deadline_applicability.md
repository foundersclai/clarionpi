# S9 — Apply intake facts to deadline candidates

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-09
- Slice ID: S9
- Dependencies: S1
- Mergeability: independent
- Deployment: safe
- Safe intermediate state: standard matter creation persists only rule-pack candidates applicable to typed intake.
- Final integration owner: S19

## Goal and non-goals

- Goal: suppress the Arizona public-entity notice candidate for private-party matters.
- Observable success: WI-2 intake context deterministically controls audited deadline applicability.
- Non-goals: legal audit of deadline values, new refusal wording, or public-entity matter support.
- Assumptions requiring confirmation: the current Arizona pack remains visibly counsel-unreviewed outside production.

## Live-code grounding

- Owner modules: rules pack schema/loader/fingerprint, deadline engine, matter creation route.
- Existing symbols: `compute_deadline_candidates` and WI-2 `public_entity_involved` intake.
- Consumers: persisted deadline candidates, G1 facts view, rule-pack pin/build gates.
- Contracts: jurisdiction rules, API view models, audited pack provenance.
- Compatibility: pack fingerprints cover applicability predicates and pinned consumers fail closed.

## Data flow and blast radius

Typed intake answer → pack predicate validator → deadline engine → matter creation/G1 consumer →
applicable candidates. Missing/malformed context or predicate refuses before matter, deadline, or
audit writes; a pinned pack change remains detectable.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Intake → deadlines | typed intake → applicability schema → deadline engine → matter/G1 view → candidate rows | private-party omits notice | malformed predicate refuses | explicit public-entity answer is distinct | pinned pack drift refuses consumers | no matter deadline or audit on invalid context | `happy → backend/tests/api/test_matters.py::test_create_private_party_persists_only_applicable_deadlines; negative → backend/tests/api/test_matters.py::test_malformed_applicability_pack_refuses_creation_without_matter_deadline_or_audit; edge → backend/tests/api/test_gates_api.py::test_facts_review_private_party_omits_public_entity_notice; retry/terminal → backend/tests/api/test_rule_pack_pin_boundaries.py::test_applicability_pack_change_refuses_pinned_consumers_before_writes; side effects → backend/tests/api/test_matters.py::test_deadline_context_invalid_writes_no_matter_or_audit` |

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

- Commands: focused rules/matter/G1 tests and pack fingerprint boundaries.
- Expected failures: current call site computes conditional public-entity notice without intake context.
- Observed failures: add matter/pack/intake/applicability diagnostics before changing results.
- Characterization exception: no deadline value or legal conclusion changes in this slice.
- LLM integration omission: deadline computation is deterministic.

## Implementation sequence

1. Add diagnostic evidence and a private-party regression.
2. Extend the extra-forbid pack schema with typed applicability predicates and fingerprint coverage.
3. Thread WI-2 intake context through matter creation into deadline computation.
4. Refuse malformed/missing required context before transactional writes.
5. Update rules/API contracts and run pinned-pack boundary tests.

## Verification and acceptance

- Private-party matters contain no public-entity notice candidate in persistence or G1 projection.
- Invalid context leaves matter, deadlines, and audit untouched.
- Pack changes invalidate pinned consumers before downstream writes.
- `make verify` passes.
