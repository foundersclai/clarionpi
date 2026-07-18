# S10 — Stabilize chronology artifact determinism

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-10
- Slice ID: S10
- Dependencies: S1
- Mergeability: independent
- Deployment: safe
- Safe intermediate state: chronology claims remain semantic-only until the byte diagnostic proves closure.
- Final integration owner: S19

## Goal and non-goals

- Goal: diagnose and remove time-dependent XLSX metadata or keep the limitation explicit and tested.
- Observable success: two separated builds either match bytes or are compared only by declared semantics.
- Non-goals: changing chronology facts, ordering rules, or workshop artifact restrictions.
- Assumptions requiring confirmation: openpyxl metadata is the only current second-boundary suspect.

## Live-code grounding

- Owner modules: package chronology builder and artifact determinism tests.
- Existing symptom: builds straddling a second can embed different workbook timestamps.
- Consumers: artifact-set hash, standard baseline, workshop verifier, evidence claims.
- Contracts: package builder byte-determinism and truthful workshop evidence.
- Compatibility: normalized workbook content remains the fallback until byte identity is proven.

## Data flow and blast radius

Chronology rows → workbook metadata diagnostic → chronology builder → artifact hash consumer →
determinism claim. The fix normalizes owned metadata before serialization; unexpected nondeterminism
fails the hash test and cannot silently upgrade the evidence claim.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Chronology rows → XLSX bytes | ordered rows → metadata normalizer → chronology builder → artifact manifest → byte or semantic baseline | separated builds match declared mode | uncontrolled metadata fails | second-boundary build stays stable | retry uses identical owned inputs | no false byte-determinism claim | `happy → backend/tests/package/test_chronology_determinism.py::test_two_separated_builds_match_declared_baseline_mode; negative → backend/tests/package/test_chronology_determinism.py::test_uncontrolled_workbook_metadata_fails_determinism_gate; edge → backend/tests/package/test_chronology_determinism.py::test_second_boundary_does_not_change_owned_workbook_bytes; retry/terminal → backend/tests/package/test_chronology_determinism.py::test_retry_uses_identical_metadata_and_rows; side effects → backend/tests/package/test_chronology_determinism.py::test_open_chip_prevents_identical_hash_claim` |

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

- Commands: focused chronology determinism tests with an intentional second-boundary delay.
- Expected failures: current workbook bytes can differ solely because of embedded timestamps.
- Observed failures: log metadata fields and byte hashes before normalization.
- Characterization exception: if another uncontrolled source remains, keep semantic comparison explicit.
- LLM integration omission: artifact serialization is deterministic and model-free.

## Implementation sequence

1. Add structured metadata/hash diagnostics and reproduce the second-boundary failure.
2. Normalize all owned workbook creation/modification metadata before save.
3. Compare workbook bytes across delayed identical builds and parse semantic equality as a guard.
4. Update the standard baseline mode only after the byte test is green.
5. Update package contract and close or constrain the existing chip truthfully.

## Verification and acceptance

- Identical inputs produce the declared deterministic mode across separated builds.
- No chronology fact, ordering, print area, or artifact restriction changes.
- Evidence copy never claims identical hashes while the byte gate is open.
- `make verify` passes.

