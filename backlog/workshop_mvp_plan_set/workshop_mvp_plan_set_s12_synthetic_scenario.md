# S12 — Generate and seal the synthetic Arizona scenario

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-12
- Slice ID: S12
- Dependencies: S1
- Mergeability: independent
- Deployment: dormant
- Safe intermediate state: generated scenario generations are inert until a later validated runtime activates one.
- Final integration owner: S19

## Goal and non-goals

- Goal: own one versioned synthetic MVA bundle with deterministic PDFs, truth, and manifest hashes.
- Observable success: regeneration is byte-identical and validators reject any drift or prohibited source.
- Non-goals: runtime loading, upload mutation, live records, OCR, or a second scenario.
- Assumptions requiring confirmation: all names, providers, identifiers, and prose are newly fictional.

## Live-code grounding

- Owner modules: new `workshop` scenario generator/validator and immutable generation publisher.
- Existing consumers to prepare for: WI-2 matter schema, upload slots, extractors, ledger, and replay catalog.
- Forbidden inputs: `samples/`, tests, real case records, PHI, and attendee documents.
- Contracts: workshop lifecycle owner, corpus ingest shapes, and scenario version/label identity.
- Compatibility: no standard runtime imports the generated bundle.

## Data flow and blast radius

Checked-in synthetic specification → schema/content validator → deterministic generator → sealed
generation owner → manifest/truth bundle. Generation stages to a new directory and atomically moves
an authenticated pointer only after every file hash, path, page count, and truth constraint passes.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | Synthetic spec → sealed bundle | owned spec → bundle/content validator → scenario generator → future loader → immutable generation | regeneration matches bytes | invalid bundle refuses | empty or extra key is invalid | crash before pointer rename preserves prior active | no PHI sample-derived bytes or partial active generation | `happy → backend/tests/workshop/test_scenario_bundle.py::test_regeneration_is_identical_and_failed_generation_preserves_active_pointer; negative → backend/tests/workshop/test_scenario_bundle.py::test_manifest_rejects_invalid_bundle; edge → backend/tests/workshop/test_scenario_bundle.py::test_empty_or_extra_key_bundle_is_invalid; retry/terminal → backend/tests/workshop/test_scenario_bundle.py::test_crash_before_or_after_active_pointer_rename_reconciles_without_guessing; side effects → backend/tests/workshop/test_scenario_bundle.py::test_synthetic_identifier_and_demand_prose_scan_is_clean` |

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

- Commands: generator golden tests, manifest mutation tests, and source/prose scanners.
- Expected failures: no current owner enforces a sealed deterministic synthetic scenario contract.
- Observed failures: diagnostics report only generation IDs, paths, sizes, and hashes.
- Characterization exception: the scenario asserts fixed synthetic truth, not general extraction quality.
- LLM integration omission: scenario generation is local and model-free.

## Implementation sequence

1. Define the closed scenario/truth/manifest schemas and prohibited-source scanner.
2. Generate the nine text-layer PDFs and exact matter, money, risk, exhibit, and demand truth.
3. Validate paths, media types, counts, hashes, duplicates, and cross-file identities.
4. Publish immutable scenario generations with an atomic pointer and crash reconciliation.
5. Add generator/toolchain locks, contract docs, and deterministic goldens.

## Verification and acceptance

- Regeneration produces identical files and a version bump is required for changed bytes.
- Invalid or prohibited inputs leave the prior active generation unchanged.
- The bundle contains no PHI, sample-derived content, or live-provider dependency.
- `make verify` passes.
