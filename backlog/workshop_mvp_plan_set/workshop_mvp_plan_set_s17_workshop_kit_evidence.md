# S17 — Build the workshop kit and evidence funnel

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-17
- Slice ID: S17
- Dependencies: S1, S13
- Mergeability: ordered
- Deployment: dormant
- Safe intermediate state: materials and evidence views cannot create product authority or legal approval.
- Final integration owner: S19

## Goal and non-goals

- Goal: provide truthful presenter materials, structured feedback, export guidance, and paid-review outreach.
- Observable success: a non-builder can present limitations and collect no client information.
- Non-goals: endorsements, cofounder equity, legal approval, live-client intake, or privileged data collection.
- Assumptions requiring confirmation: counsel outreach uses a separate written paid-review engagement.

## Live-code grounding

- Owner modules: repo-owned workshop slides, scripts, forms, limitations, and runbook.
- Existing seams: the S13 evidence export schema, runtime disclosure, package inspection, and recovery cues.
- Consumers: presenter, workshop attorneys, paid-review candidate, and release evidence gate.
- Contracts: materials consume the body-free S13 export and keep qualitative notes strictly separate.
- Compatibility: qualitative notes never become machine evidence or workflow state.

## Data flow and blast radius

Body-free S13 evidence export → material-schema validator → workshop-material owner → presenter and
review candidate → versioned kit. Materials display server-derived evidence without copying facts,
prompts, or confidential content and never manufacture completion, approval, or legal authority.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Evidence export → workshop kit | S13 closed export → material validator → kit owner → presenter/reviewer → versioned materials | kit matches export schema | prohibited client-content field refuses | interrupted evidence is labeled ineligible | changed export schema requires kit version update | no API mutation PHI legal approval or endorsement claim | `happy → backend/tests/workshop/test_materials.py::test_materials_match_closed_evidence_export_schema; negative → backend/tests/workshop/test_materials.py::test_materials_reject_client_content_and_confidential_fields; edge → backend/tests/workshop/test_materials.py::test_interrupted_evidence_is_visibly_ineligible; retry/terminal → backend/tests/workshop/test_materials.py::test_export_schema_change_requires_material_version_update; side effects → backend/tests/workshop/test_materials.py::test_material_validation_has_no_api_or_workflow_side_effect` |

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

- Commands: deterministic material-schema, limitation-copy, and no-confidential-field checks.
- Expected failures: current materials do not yet consume a closed versioned evidence export.
- Observed failures: validate only schema/version and approved display fields, never content bodies.
- Characterization exception: manual timing and comprehension remain human acceptance evidence.
- LLM integration omission: materials and export require no model call.

## Implementation sequence

1. Bind material rendering to the closed versioned S13 evidence-export schema.
2. Add versioned slides, presenter scripts, recovery cues, scope/limitations, and feedback form.
3. Add the paid Arizona review brief and separate product-review/advisory/cofounder funnel stages.
4. Add screenshots/recording fallback and validate the no-client-information warning everywhere.
5. Add deterministic schema/copy scans and a presenter handoff checklist.

## Verification and acceptance

- Materials display only hash-verified body-free exports and never copy qualitative notes into evidence.
- Interrupted/indeterminate/test-only runs are visibly ineligible in every presenter surface.
- Materials distinguish demonstration, intended attorney approval, and actual no-lawyer review.
- `make verify` and document consistency checks pass.
