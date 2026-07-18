# S11 — Add fail-closed workshop runtime composition

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-11
- Slice ID: S11
- Dependencies: S1
- Mergeability: independent
- Deployment: dormant
- Safe intermediate state: workshop boot remains refused until explicit validated capabilities are complete.
- Final integration owner: S19

## Goal and non-goals

- Goal: compose a loopback-only session-secured workshop app with explicit services and disclosure.
- Observable success: invalid profile/provider/auth/origin combinations fail during module construction.
- Non-goals: loading scenario data, persisting demo identity, seeding, or enabling package generation.
- Assumptions requiring confirmation: standard dev remains the default when the profile is omitted.

## Live-code grounding

- Owner modules: core config/app composition/provider factory, main/auth middleware, runtime API, frontend shell.
- Existing seam: `LLM_PROVIDER` is read directly in `backend/app/core/llm_provider.py`.
- Consumers: every model-backed engine, storage/session wiring, frontend disclosure, launch commands.
- Contracts: new core runtime composition plus core telemetry, API view models, and auth ADR.
- Compatibility: `APP_ENV` keeps its four values and standard profile behavior remains unchanged.

## Data flow and blast radius

Environment/config → construction-time validator → explicit AppServices owner → API/frontend
consumer → bound loopback process. Invalid combinations construct no engine, app, provider, session,
or storage sink; process-global settings access is removed from lower layers.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Runtime config → served app | environment → Settings/profile validator → create_app services → API/frontend → loopback runtime | dev workshop composition passes | prod test staging or live provider refuses | blank URL and untrusted origin refuse | lifespan-off cannot bypass validation | no engine provider storage or app on invalid config | `happy → backend/tests/workshop/test_workshop_config.py::test_dev_workshop_replay_session_configuration_passes_with_constructed_attestation; negative → backend/tests/workshop/test_workshop_config.py::test_invalid_profile_combination_refuses_at_module_construction; edge → backend/tests/workshop/test_workshop_config.py::test_unknown_blank_or_url_runtime_values_are_refused; retry/terminal → backend/tests/workshop/test_workshop_config.py::test_lifespan_off_cannot_bypass_workshop_validation; side effects → backend/tests/workshop/test_workshop_config.py::test_static_invalid_configuration_constructs_no_engine_sink_or_app` |

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

- Commands: config/app-construction subprocess tests, auth/Origin tests, and frontend runtime-view tests.
- Expected failures: direct provider environment reads and incomplete composition can bypass central validation.
- Observed failures: add construction diagnostics without secrets before moving provider selection.
- Characterization exception: constructed test settings exercise workshop logic without booting test-plus-workshop.
- LLM integration omission: provider instances are scripted or null during composition tests.

## Implementation sequence

1. Accept the runtime ADR/contract and add closed profile/provider/bind/origin settings.
2. Introduce explicit immutable AppServices composition and remove lower-layer global settings access.
3. Enforce session auth, exact Origin, loopback bind, no proxy headers, and local asset requirements.
4. Add a secret-free no-store runtime view and authoritative persistent frontend disclosure surface.
5. Add launch-config and two-app isolation tests; update core/API/frontend contracts.

## Verification and acceptance

- Invalid combinations fail at import/application construction even with lifespan disabled.
- Standard dev has no demo claim and two composed apps retain separate service graphs.
- Workshop runtime truth is backend-authoritative, secret-free, and cache-disabled.
- `make verify` passes.

