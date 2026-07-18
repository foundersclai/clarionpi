# S19 — Prove the full isolated workshop flow

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-18
- Slice ID: S19
- Dependencies: S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12, S13, S14, S15, S16, S17, S18
- Mergeability: atomic
- Deployment: atomic_release
- Safe intermediate state: no release tag or workshop claim exists until every automated and manual exit check passes.
- Final integration owner: S19

## Goal and non-goals

- Goal: integrate the sealed scenario through real session/Origin upload, gates, replay, package, and evidence export.
- Observable success: disposable flows, offline raw run, artifact verification, and five timed rehearsals pass.
- Non-goals: live-client readiness, general OCR/model quality, production hosting, or legal correctness.
- Assumptions requiring confirmation: local `main` is published and the exact rehearsed commit is tagged only after acceptance.

## Live-code grounding

- Owner modules: integration tests, frontend workbench truth, workshop launcher/runbook, CI Postgres tier.
- Producers: every S1-S18 contract; consumers: real REST/SSE/UI/package/provenance and evidence paths.
- Existing baseline: M5/M6 HTTP flow, session/Origin middleware, package parsers, frontend Vitest.
- Contracts: all module contracts plus definition of done, testing policy, and workshop exit gate.
- Compatibility: standard profile remains green and receives no workshop disclosure or artifact overlay.

## Data flow and blast radius

Sealed workspace/session → full contract validators → composed ClarionPI owners → REST/SSE/frontend
consumers → restricted package and evidence export. Each disposable root is destroyed after the run;
Wi-Fi-off and egress traps prove no external dependency, while terminal run truth precedes UI success.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Isolated app → workshop evidence | sealed scenario/config → full admission/fence validators → composed app owners → HTTP/SSE/frontend → restricted package/evidence | three full flows reach package | external network and false claims refuse | duplicate reconcile uses zero calls | disconnect preserves durable terminal truth | no leaked roots provider objects unresolved tokens or workshop semantics in standard | `happy → backend/tests/workshop/test_workshop_e2e.py::test_full_http_flow_reaches_restricted_package; negative → backend/tests/workshop/test_workshop_e2e.py::test_full_flow_egress_trap_allows_loopback_and_refuses_external_connections; edge → backend/tests/workshop/test_workshop_e2e.py::test_showcase_duplicate_resolution_blocks_g1_until_zero_call_phase0_reconcile_then_preserves_16_plus_26_inventory; retry/terminal → backend/tests/workshop/test_workshop_e2e.py::test_every_stream_has_one_terminal_success_after_durable_state; side effects → backend/tests/workshop/test_workshop_e2e.py::test_full_flow_has_no_forbidden_side_effect_or_claim` |

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

- Commands: full isolated HTTP suite, Postgres integration, frontend tests, artifact verifier, and manual runbook.
- Expected failures: integration stays red until every predecessor contract and sealed asset is present.
- Observed failures: record stage/run/hash/count/result diagnostics without source or model bodies.
- Characterization exception: manual timing and attorney comprehension are recorded separately from automation.
- LLM integration omission: replay is the required provider; live-model quality is explicitly excluded.

## Implementation sequence

1. Assemble a disposable `create_app` full flow using fresh DB/storage/services and real session/Origin boundaries.
2. Assert exact matter, upload, call-vector, facts, money, risks, exhibits, gates, provenance, and artifacts.
3. Add egress isolation, SSE disconnect/transport-loss truth, and no-cross-app leakage tests.
4. Run the Postgres lock tier and pinned no-network artifact renderer in CI and on the workshop laptop.
5. Complete raw offline run, five restart cycles, five timed rehearsals, runbook handoff, and release evidence.

## Verification and acceptance

- Three disposable flows and all exact scenario assertions pass with no external network or live key.
- All four artifacts parse, remain restricted, and source reads produce expected PHI-access audits.
- Five manual rehearsals finish under ten minutes with at least sixty seconds margin.
- `make verify`, the Postgres integration tier, and the complete source exit checklist pass before tagging.
