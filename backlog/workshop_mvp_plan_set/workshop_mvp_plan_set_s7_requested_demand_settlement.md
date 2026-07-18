# S7 — Settle the final requested demand

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-07
- Slice ID: S7
- Dependencies: S5, S6
- Mergeability: ordered
- Deployment: safe
- Safe intermediate state: new plans require exact elections while legacy unbound plans cannot gain package authority.
- Final integration owner: S19

## Goal and non-goals

- Goal: persist the operator-selected demand as immutable human-origin provenance through G2.5.
- Observable success: the exact saved amount appears in allocation, letter output, and approval bindings.
- Non-goals: choosing a legally appropriate amount, inventing attorney approval, or demo-only token rules.
- Assumptions requiring confirmation: G1.5 and G2.5 actor identities are available from committed gate records.

## Live-code grounding

- Owner modules: strategy schema/ORM, tokenizer registry, brain2 plan/allocator/renderer, orchestrator gates.
- Existing seams: `StrategyPlan.demand_amount_cents`, plan-review edit service, gate affordances, rules pack.
- Consumers: plan approval, drafting, compliance, package manifest, letter DOCX, and provenance API.
- Contracts: tokenizer, brain2, orchestrator, rules, API, money, and package builder.
- Compatibility: generic plan edits cannot bypass the dedicated settlement action.

## Data flow and blast radius

Actor-bound strategy revision → election/role validator → settlement owner → approved exact plan →
draft/package sink. Saving a final amount appends an election and plan version atomically, settles a
typed attorney-origin amount token, and keeps the gate at plan review until exact approval.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Strategy amount → exact approved plan | actor-bound revision → role/election validator → plan settlement service → G2.5 approval → demand draft | saved amount renders | null or stale amount refuses approval | omitted differs from explicit clear | idempotent retry returns original settlement | no partial election token plan gate or audit | `happy → backend/tests/api/test_drafting_api.py::test_save_final_amount_returns_refreshed_plan_and_stays_plan_review; negative → backend/tests/api/test_drafting_api.py::test_approve_refuses_null_with_demand_amount_required_and_writes_nothing; edge → backend/tests/api/test_drafting_api.py::test_save_distinguishes_required_null_from_omitted_amount; retry/terminal → backend/tests/api/test_drafting_api.py::test_gate_save_and_edit_replay_return_original_result_plan_after_later_versions; side effects → backend/tests/api/test_drafting_api.py::test_save_gate_record_or_audit_failure_rolls_back_entire_settlement` |

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

- Commands: focused plan/gate/token/renderer tests and migration checks.
- Expected failures: a non-null plan amount currently reaches letter output without the demand figure.
- Observed failures: add plan/version/token-allocation diagnostics before behavior changes.
- Characterization exception: legacy unbound plans remain historical and cannot authorize new work.
- LLM integration omission: scripted plan/draft providers prove allocation deterministically.

## Implementation sequence

1. Accept `docs/adr/0014-requested-demand-settlement.md` and add election, role, provenance, plan-binding schema.
2. Preflight/backfill legacy amount roles and forbid new mixed or unanchored v1 shapes.
3. Add immutable actor-bound elections and dedicated save-final-amount settlement under lock.
4. Require role-aware pack allocation, exact G2.5 plan approval, and honest edit affordances.
5. Render/provenance the requested amount and update all affected shared contracts.

## Verification and acceptance

- Equal values from different elections never alias and medical roles cannot satisfy demand role.
- Save, approve, draft, and package use one exact plan/election/version chain.
- Failures roll back election, token, plan, gate, audit, and run together.
- `make verify` and migration preflight tests pass.
