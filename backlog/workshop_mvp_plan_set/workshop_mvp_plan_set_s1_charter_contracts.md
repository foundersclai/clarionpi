# S1 — Freeze the Workshop MVP charter and contracts

- Parent umbrella: `backlog/workshop_mvp_plan_set/umbrella.md`
- Source IDs: SRC-01
- Slice ID: S1
- Dependencies: none
- Mergeability: independent
- Deployment: safe
- Safe intermediate state: documentation and contract registrations add no runtime capability.
- Final integration owner: S19
<!-- sdlc-tier-assessment:start -->
## SDLC tier assessment
- SDLC-Tier: 3
- SDLC-Minimum-Tier: 3
- SDLC-Tier-Status: APPROVED
- SDLC-Tier-Assessor: Codex, live repository context
- SDLC-Tier-Content-SHA256: 77302377dd5152741f9f2ba98d79cd8bce5edca65096ef2345c21345b94a41cd
- SDLC-Tier-Base-SHA: d50586d3026d80f9da28b54a608d27cf368d431c
- SDLC-Tier-Triggers: module ownership changes; cross-module contract ownership registration
- SDLC-Tier-Approval: user-approved in thread
- SDLC-Tier-Approval-Rationale: recommended
- SDLC-Tier-Degraded-Assurance: NONE
- SDLC-Tier-Revalidation: unchanged-tier — consensus corrections preserve Tier 3 scope
<!-- sdlc-tier-assessment:end -->

## Goal and non-goals

- Goal: accept the workshop boundary plus the four shared prerequisite decisions, freeze the
  tenant-key discipline, and declare the future module owners without claiming that their runtime
  packages already exist.
- Observable success: ADR-0013 through ADR-0017, the contract hubs, the readiness memo, and the
  synthetic-source charter distinguish workshop evidence from R2 readiness; `make hub-check`
  rejects a missing charter artifact or a premature live contract registration.
- Non-goals: runtime profile/package code, live `CONTRACTS.md` rows for modules that do not exist,
  demo data, legal attestation copy, or any allocation/change of reserved ADR-0009.
- Confirmed numbering: allocate ADR-0013 through ADR-0017 to the five decisions below and leave
  ADR-0009 absent/reserved.

## Live-code grounding

- Owner surfaces: `docs/adr`, `docs/system_contract.md`, `docs/module_contracts/README.md`,
  `CONTRACTS.md`, `backlog/pi/10_implementation_readiness.md`, and `workshop/README.md`.
- Existing symbols: `scripts/hub_check.py::parse_contracts_table` and
  `scripts/hub_check.py::check_contracts_table`; the live registry currently validates only that
  each table row's module path and contract document both exist.
- Planned hub symbols: add `FUTURE_WORKSHOP_OWNERS`, `WORKSHOP_TENANT_KEY_GROUPS`,
  `WORKSHOP_REFERENCE_GROUPS`, and `check_workshop_charter(repo_root)` in
  `scripts/hub_check.py`; thread `repo_root` through the existing checks and
  `main(repo_root=REPO_ROOT)` so tests exercise the same aggregate/diagnostic path as the CLI.
- Consumers: `make hub-check`, `make verify`, reviewers, later migrations, and every downstream
  child plan.
- Test destination: add `backend/tests/test_hub.py`; no repository-level hub-test module exists.
- Contracts: root `AGENTS.md`, `CONTRACTS.md`, `docs/system_contract.md`, and the module-contract
  index require boundary text, ownership registration, and proving tests to land together.
- Compatibility: R0-R4 and held ADR/WI state remain unchanged.

## Data flow and blast radius

Source snapshot → five accepted ADRs → future-owner declarations outside the live registry table →
downstream slice/package creation → same-PR module contract + live registry row → hub gate.
`CONTRACTS.md` table rows remain truth about existing paths: S1 adds no row for
`app.core.matter_access` or `app.workshop.lifecycle`. The hub checker separately requires the five
ADRs, readiness/source rules, future-owner declarations, and preserved ADR-0009 reservation. Later
slices move an owner into the live table only when both its package and contract document land.
For each future owner, the validator treats declaration-only as the S1 happy state, complete
module+contract+row activation as the later happy state, and module-only,
module+contract-without-row, or row-with-missing-contract as deterministic terminal failures.
`FUTURE_WORKSHOP_OWNERS` freezes the activation tuples:
`app.core.matter_access` → implementation marker `backend/app/core/matter_access.py`, contract
`docs/module_contracts/app.core.matter_access.md`, registry module path `backend/app/core`; and
`app.workshop.lifecycle` → marker `backend/app/workshop/lifecycle.py`, contract
`docs/module_contracts/app.workshop.lifecycle.md`, registry module path `backend/app/workshop`.

## Decision and tenant-key allocation

- `docs/adr/0013-workshop-mvp-boundary.md`: synthetic-only evidence, permanent distinction from R2,
  ADR sequence, global full-scope-key rule, all-null/all-present compound references, Postgres
  `MATCH FULL` where supported, and migration preflight refusal for cross-scope/partial-null data.
- `docs/adr/0014-requested-demand-settlement.md`: election/token/plan settlement and exact
  `StrategyPlan` ID/version binding for every new `DemandDraft`.
- `docs/adr/0015-operation-and-generation-publication.md`: durable operation ownership plus
  corpus/registry/evidence/analysis generation identity and publication ordering.
- `docs/adr/0016-draft-compliance-authority.md`: immutable draft/finding history and exact G3
  approval without redefining ADR-0014's plan binding.
- `docs/adr/0017-artifact-publication-recovery.md`: reserve/stage/publish artifact authority,
  collision-free keys, and recovery ownership.
- ADR-0013 carries the source snapshot's complete candidate-key/reference inventory; ADR-0014
  through ADR-0017 link their owned subsets back to it. A bare UUID or matter-local integer version
  is never a tenant-safe foreign-key substitute.

The hub contract freezes that inventory in nine exact candidate-key groups: tenant roots/decision
authority; strategy/settlement; generation/publication; analysis facts; draft/compliance;
operations/telemetry; ingest; workshop authority; and artifacts. It freezes all 21 reference clauses
in five exact groups: strategy/authority (clauses 1–6); budget/run (7–10); corpus/evidence (11–13);
analysis/current pointers (14–17); and upload/publication (18–21). The ADR remains the human-readable
authority; the two stdlib tuples are its drift gate and are compared as exact ordered column/ref
sets, so a missing, extra, reordered, or altered entry fails visibly.

Exact candidate-key group contents (48 shapes across 31 model families):

- tenant roots/decision authority: `Matter(firm_id,id)`,
  `Matter(firm_id,id,matter_purpose)`,
  `Matter(firm_id,id,matter_purpose,demo_scenario_id,demo_scenario_version,demo_label_version)`,
  `User(firm_id,id)`, `GateRecord(firm_id,matter_id,id)`,
  `GateRecord(firm_id,matter_id,id,evidence_binding_state,result_evidence_version,result_evidence_head_sha256,result_analysis_binding_state,result_analysis_generation_id,result_evidence_registry_version)`,
  `GateRecord(firm_id,matter_id,id,result_binding_state,result_draft_id,result_draft_version,compliance_head_sha256)`;
- strategy/settlement: `StrategyInputs(firm_id,matter_id,id,version)`,
  `RequestedDemandElection(firm_id,matter_id,id,version)`,
  `StrategyPlan(firm_id,matter_id,id,version)`,
  `StrategyPlan(firm_id,matter_id,id,version,source_operation_run_id)`;
- generation/publication: `CorpusVersion(firm_id,matter_id,version)`,
  `EvidenceVersion(firm_id,matter_id,version)`,
  `EvidenceVersion(firm_id,matter_id,version,head_sha256,analysis_binding_state,analysis_generation_id)`,
  `RegistryVersion(firm_id,matter_id,version)`,
  `RegistryVersion(firm_id,matter_id,version,source_operation_run_id)`,
  `AnalysisGeneration(firm_id,matter_id,id)`,
  `AnalysisGeneration(firm_id,matter_id,id,result_registry_version)`,
  `AnalysisGeneration(firm_id,matter_id,id,analysis_operation_run_id,result_registry_version)`;
- analysis facts: `MedicalEncounter(firm_id,matter_id,id)`,
  `EncounterNarrative(firm_id,matter_id,encounter_id,analysis_generation_id)`,
  `RiskFlag(firm_id,matter_id,id)`;
- draft/compliance: `DemandDraft(firm_id,matter_id,id,version)`,
  `DemandDraft(firm_id,matter_id,version)`,
  `DemandDraft(firm_id,matter_id,id,version,owning_operation_run_id)`,
  `ComplianceFinding(firm_id,matter_id,id)`,
  `ComplianceFindingRevision(firm_id,matter_id,finding_id,revision)`,
  `ComplianceFindingRevision(firm_id,matter_id,draft_id,draft_version,finding_id,revision)`,
  `ComplianceFindingRevision(firm_id,matter_id,draft_id,draft_version,finding_id,revision,source_operation_run_id)`;
- operations/telemetry: `MatterOperationRun(firm_id,matter_id,id)`,
  `MatterBudget(firm_id,matter_id)`, `AuditEvent(firm_id,id)`,
  `ProviderInvocation(firm_id,matter_id,attempt_id)`, `LlmCall(firm_id,matter_id,attempt_id)`;
- ingest: `CaseDocument(firm_id,matter_id,id)`,
  `Phase0RunDocument(firm_id,matter_id,run_id,document_id)`,
  `UploadSession(firm_id,matter_id,id)`, `UploadSlot(firm_id,matter_id,session_id,id)`,
  `UploadBlobAttempt(firm_id,matter_id,session_id,slot_id,id)`;
- workshop authority: `WorkshopEvidenceRun(firm_id,id)`,
  `WorkshopEvidenceRun(firm_id,matter_id,id)`, `WorkshopScenarioSeal(firm_id,matter_id)`,
  `WorkshopScenarioSeal(firm_id,matter_id,id)`;
- artifacts: `ArtifactReuseRecord(firm_id,matter_id,id,operation_run_id,artifact_set_id)`,
  `ArtifactSet(firm_id,matter_id,id)`, `ArtifactSet(firm_id,matter_id,id,operation_run_id)`,
  `ArtifactPublication(firm_id,matter_id,id)`,
  `ArtifactPublication(firm_id,matter_id,id,state)`.

Exact reference-group contents (21 clauses):

- strategy/authority: strategy revision→actor/gate; election→revision; plan/token→election;
  plan→exact composite G2a evidence result; draft→approved plan; gate result→plan/draft;
- budget/run: budget→Matter; warning audit→AuditEvent;
  resumed run/LlmCall/PlanEmitAttempt→run; workshop UploadSession/operation/ProviderInvocation/
  GateRecord/ArtifactSet/CorpusVersion/EvidenceVersion→evidence run;
- corpus/evidence: corpus head/processed pointers→corpus version;
  evidence head/G2a result→evidence version; Phase0 membership→CaseDocument;
- analysis/current pointers: analysis narrative→MedicalEncounter/generation;
  analysis child/current pointer→generation; carried risk flag→prior same-matter flag;
  Matter→current draft;
- upload/publication: seal→active/sealed session; slot/session→blob attempt;
  run→typed result plus its producing run (or an explicit typed preexisting/reuse result record);
  ArtifactSet/publication→draft/G3 GateRecord/run/publication.

S1 characterizes rather than repairs the live ORM: 17 model families exist with scalar/bare parent
keys and 14 planned families are absent; there is no composite `ForeignKeyConstraint`, `MATCH FULL`,
or migration preflight for the frozen shapes. Downstream migration slices own those changes. S1
must not edit `backend/app/models/orm.py` or `backend/alembic/versions/`.

## Boundary and adversarial test matrix

| ID | Surface/path | Source of truth → validator → owner → consumer → sink | Happy | Negative | Edge | Retry/fallback/terminal failure | Side effects to assert present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | ADR charter and shared decisions | source WMVP-00 → five ADR files → hub charter validator → downstream slices | exact ADR-0013–0017 ownership sequence passes | each missing/wrong ADR path fails separately | ADR-0009 remains absent/reserved | dependency links force 0015 before operation/generation work, 0014 before settlement, 0016 before draft/compliance, and 0017 before publication | no shared decision is folded into ADR-0013 and no legal attestation is created | `happy → backend/tests/test_hub.py::test_workshop_adr_sequence_and_decision_owners_are_registered; negative missing → backend/tests/test_hub.py::test_each_missing_workshop_adr_fails_hub_check[0013], backend/tests/test_hub.py::test_each_missing_workshop_adr_fails_hub_check[0014], backend/tests/test_hub.py::test_each_missing_workshop_adr_fails_hub_check[0015], backend/tests/test_hub.py::test_each_missing_workshop_adr_fails_hub_check[0016], backend/tests/test_hub.py::test_each_missing_workshop_adr_fails_hub_check[0017]; negative wrong owner → backend/tests/test_hub.py::test_wrong_workshop_adr_assignment_fails_hub_check[0014-requested-demand], backend/tests/test_hub.py::test_wrong_workshop_adr_assignment_fails_hub_check[0015-operation-generation], backend/tests/test_hub.py::test_wrong_workshop_adr_assignment_fails_hub_check[0016-draft-compliance], backend/tests/test_hub.py::test_wrong_workshop_adr_assignment_fails_hub_check[0017-artifact-publication]; edge → backend/tests/test_hub.py::test_adr_0009_stays_reserved; dependency/terminal → backend/tests/test_hub.py::test_shared_adr_dependency_is_explicit[0015-before-operation-generation], backend/tests/test_hub.py::test_shared_adr_dependency_is_explicit[0014-before-settlement], backend/tests/test_hub.py::test_shared_adr_dependency_is_explicit[0016-before-draft-compliance], backend/tests/test_hub.py::test_shared_adr_dependency_is_explicit[0017-before-publication]; forbidden folding → backend/tests/test_hub.py::test_shared_decision_is_not_folded_into_workshop_adr[0014], backend/tests/test_hub.py::test_shared_decision_is_not_folded_into_workshop_adr[0015], backend/tests/test_hub.py::test_shared_decision_is_not_folded_into_workshop_adr[0016], backend/tests/test_hub.py::test_shared_decision_is_not_folded_into_workshop_adr[0017]; forbidden attestation → backend/tests/test_hub.py::test_s1_adds_no_legal_attestation` |
| BM-02 | Future owner declaration → live contract registry | ADR ownership → `docs/system_contract.md`/module index/`CONTRACTS.md` prose → `parse_contracts_table`/`check_workshop_charter` → later package PR → hub gate | declaration-only passes now; complete module+contract+row activation passes later | module-only fails; module+contract without row fails; row with missing contract fails | all existing live registry rows remain byte-for-byte present | missing declaration fails; every partial activation returns terminal failure until completed | hub checker remains stdlib-only and imports no `backend/app` code | `happy declaration → backend/tests/test_hub.py::test_future_workshop_owner_is_declared_without_live_registry_row[app.core.matter_access], backend/tests/test_hub.py::test_future_workshop_owner_is_declared_without_live_registry_row[app.workshop.lifecycle]; happy activation → backend/tests/test_hub.py::test_complete_future_owner_activation_passes_hub_check[app.core.matter_access], backend/tests/test_hub.py::test_complete_future_owner_activation_passes_hub_check[app.workshop.lifecycle]; negative module-only → backend/tests/test_hub.py::test_partial_future_owner_activation_fails_hub_check[app.core.matter_access-module-only], backend/tests/test_hub.py::test_partial_future_owner_activation_fails_hub_check[app.workshop.lifecycle-module-only]; negative module+contract/no-row → backend/tests/test_hub.py::test_partial_future_owner_activation_fails_hub_check[app.core.matter_access-module-contract-no-row], backend/tests/test_hub.py::test_partial_future_owner_activation_fails_hub_check[app.workshop.lifecycle-module-contract-no-row]; negative row/missing-contract → backend/tests/test_hub.py::test_partial_future_owner_activation_fails_hub_check[app.core.matter_access-row-missing-contract], backend/tests/test_hub.py::test_partial_future_owner_activation_fails_hub_check[app.workshop.lifecycle-row-missing-contract]; edge → backend/tests/test_hub.py::test_s1_preserves_existing_contract_registry_rows; terminal missing declaration → backend/tests/test_hub.py::test_missing_future_owner_declaration_fails_hub_check[app.core.matter_access], backend/tests/test_hub.py::test_missing_future_owner_declaration_fails_hub_check[app.workshop.lifecycle]; forbidden import → backend/tests/test_hub.py::test_hub_check_has_no_backend_app_imports` |
| BM-03 | Evidence/readiness boundary and synthetic source rule | ADR-0013 → readiness overlay + `workshop/README.md` → hub charter validator → reviewers/workshop tooling | R1 overlay and owned-synthetic-only source rule pass | missing readiness overlay fails; missing source rule fails | R2 entry/exit text remains unchanged; `samples/`, tests, and real records are forbidden inputs | later readiness edits cannot use workshop evidence to close legal/PHI/ethics/live-pilot gates | no runtime workshop package, import, route, profile, or capability is added in S1 | `happy overlay → backend/tests/test_hub.py::test_workshop_readiness_overlay_is_present; happy source → backend/tests/test_hub.py::test_owned_synthetic_source_rule_is_present; negative overlay → backend/tests/test_hub.py::test_missing_workshop_readiness_overlay_fails_hub_check; negative source → backend/tests/test_hub.py::test_missing_synthetic_source_rule_fails_hub_check; edge readiness → backend/tests/test_hub.py::test_r2_entry_and_exit_criteria_are_unchanged; edge inputs → backend/tests/test_hub.py::test_workshop_source_is_forbidden[samples], backend/tests/test_hub.py::test_workshop_source_is_forbidden[tests], backend/tests/test_hub.py::test_workshop_source_is_forbidden[real-records]; retry/terminal → backend/tests/test_hub.py::test_workshop_evidence_cannot_close_r2_gates[legal], backend/tests/test_hub.py::test_workshop_evidence_cannot_close_r2_gates[phi], backend/tests/test_hub.py::test_workshop_evidence_cannot_close_r2_gates[ethics], backend/tests/test_hub.py::test_workshop_evidence_cannot_close_r2_gates[live-pilot]; forbidden runtime effects → backend/tests/test_hub.py::test_s1_adds_no_runtime_workshop_surface[package], backend/tests/test_hub.py::test_s1_adds_no_runtime_workshop_surface[import], backend/tests/test_hub.py::test_s1_adds_no_runtime_workshop_surface[route], backend/tests/test_hub.py::test_s1_adds_no_runtime_workshop_surface[profile], backend/tests/test_hub.py::test_s1_adds_no_runtime_workshop_surface[capability]` |
| BM-04 | Frozen tenant-key/reference contract → current legacy ORM and downstream migrations | source WMVP-00 inventory → ADR-0013 exact sets → `WORKSHOP_TENANT_KEY_GROUPS`/`WORKSHOP_REFERENCE_GROUPS` → `check_workshop_charter` → migration slices/hub gate | all nine candidate-key groups and five reference groups match exactly; current present/absent/scalar-key legacy shapes are characterized | missing, extra, reordered, or altered candidate/reference entry fails | cross-firm, same-firm/different-matter, mixed-column, bare-UUID, and bare matter-local version substitutes are forbidden by contract | all-null/all-present, Postgres `MATCH FULL`, duplicate-parent, cross-scope, partial-null, and WMVP-01D RegistryVersion preflight obligations are present | S1 adds no ORM constraint or Alembic migration | `happy keys → backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[tenant-roots-decision], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[strategy-settlement], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[generation-publication], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[analysis-facts], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[draft-compliance], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[operations-telemetry], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[ingest], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[workshop-authority], backend/tests/test_hub.py::test_tenant_key_candidate_group_is_complete[artifacts]; happy refs → backend/tests/test_hub.py::test_tenant_reference_group_is_complete[strategy-authority], backend/tests/test_hub.py::test_tenant_reference_group_is_complete[budget-run], backend/tests/test_hub.py::test_tenant_reference_group_is_complete[corpus-evidence], backend/tests/test_hub.py::test_tenant_reference_group_is_complete[analysis-current], backend/tests/test_hub.py::test_tenant_reference_group_is_complete[upload-publication]; happy characterization → backend/tests/test_hub.py::test_current_tenant_key_legacy_shape_is_characterized[present-families], backend/tests/test_hub.py::test_current_tenant_key_legacy_shape_is_characterized[absent-families], backend/tests/test_hub.py::test_current_tenant_key_legacy_shape_is_characterized[scalar-parent-fks], backend/tests/test_hub.py::test_current_tenant_key_legacy_shape_is_characterized[matter-local-uniques], backend/tests/test_hub.py::test_current_tenant_key_legacy_shape_is_characterized[integer-fences]; negative inventory → backend/tests/test_hub.py::test_tenant_contract_inventory_drift_fails_hub_check[missing], backend/tests/test_hub.py::test_tenant_contract_inventory_drift_fails_hub_check[extra], backend/tests/test_hub.py::test_tenant_contract_inventory_drift_fails_hub_check[reordered], backend/tests/test_hub.py::test_tenant_contract_inventory_drift_fails_hub_check[altered-column], backend/tests/test_hub.py::test_tenant_contract_inventory_drift_fails_hub_check[missing-reference]; edge scope → backend/tests/test_hub.py::test_tenant_key_contract_rejects_scope_mix[cross-firm], backend/tests/test_hub.py::test_tenant_key_contract_rejects_scope_mix[same-firm-different-matter], backend/tests/test_hub.py::test_tenant_key_contract_rejects_scope_mix[mixed-columns]; edge bare keys → backend/tests/test_hub.py::test_tenant_key_contract_rejects_bare_substitute[uuid], backend/tests/test_hub.py::test_tenant_key_contract_rejects_bare_substitute[matter-local-version]; terminal obligations → backend/tests/test_hub.py::test_tenant_key_migration_obligation_is_frozen[all-null-or-all-present], backend/tests/test_hub.py::test_tenant_key_migration_obligation_is_frozen[postgres-match-full], backend/tests/test_hub.py::test_tenant_key_migration_obligation_is_frozen[duplicate-parent-preflight], backend/tests/test_hub.py::test_tenant_key_migration_obligation_is_frozen[cross-scope-preflight], backend/tests/test_hub.py::test_tenant_key_migration_obligation_is_frozen[partial-null-preflight], backend/tests/test_hub.py::test_tenant_key_migration_obligation_is_frozen[registry-version-intermediate-head]; forbidden schema effects → backend/tests/test_hub.py::test_s1_tenant_contract_adds_no_schema_change[orm], backend/tests/test_hub.py::test_s1_tenant_contract_adds_no_schema_change[alembic]` |
| BM-05 | Charter validator → CLI aggregate/diagnostics | ADR/owner/readiness/source/tenant contract files → `check_workshop_charter(repo_root)` → `main(repo_root)` → stderr + process status → `make hub-check` | complete charter returns 0 and one deterministic OK line | every charter-validator failure category returns 1 with its stable diagnostic | multiple failures are emitted in deterministic order against the supplied repo root | aggregate never reports partial success and remains fail-visible on every invocation | no traceback and no filesystem write | `happy → backend/tests/test_hub.py::test_hub_main_returns_zero_for_complete_charter; negative → backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[missing-adr], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[wrong-adr-owner], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[missing-adr-dependency], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[shared-decision-folded], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[legal-attestation-present], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[adr-0009-allocated], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[missing-future-owner], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[partial-owner-activation], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[missing-readiness-overlay], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[r2-criteria-changed], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[missing-source-rule], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[forbidden-source-input], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[workshop-evidence-closes-gate], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[tenant-inventory-drift], backend/tests/test_hub.py::test_hub_main_returns_one_with_deterministic_charter_diagnostic[missing-tenant-obligation]; edge → backend/tests/test_hub.py::test_hub_main_orders_multiple_charter_diagnostics, backend/tests/test_hub.py::test_hub_main_uses_supplied_repo_root; terminal → backend/tests/test_hub.py::test_hub_main_never_reports_partial_success; forbidden effects → backend/tests/test_hub.py::test_hub_main_failure_has_no_side_effect[traceback], backend/tests/test_hub.py::test_hub_main_failure_has_no_side_effect[filesystem-write]` |

## Independent matrix-completeness review

The downstream per-slice consensus review fills this scaffold against the implementation base.

<!-- matrix-attestation:start -->
- Reviewer/context: fresh Codex subagent, read-only, neutral prompt, no correction history
- Matrix-Completeness-Gate: BLOCKED
- Matrix-Deferred-Findings: NONE
- Matrix-Review-Content-SHA256: 988f7e8240f1b02eaebdddbdf8ffb44ca6f33378079108ee24c8c96690bc44e8
- Matrix-Review-Base-SHA: d50586d3026d80f9da28b54a608d27cf368d431c
- Matrix-Review-Worktree: clean-except-plan
- Changed seams and fallback/legacy paths audited: BLOCKED — legacy hub failures and two reachable partial future-owner activation states are unmapped
- Every populated axis → exact deterministic test mapping confirmed: BLOCKED — reference drift variants and the ADR-0013 wrong-owner path lack exact tests
- Producer failure + consumer response pairs confirmed: BLOCKED — existing AGENTS/CONTRACTS/module/contract-doc failures are not allocated through the changed `main(repo_root)` aggregate
- Forbidden side-effect assertions confirmed: PASS for all currently mapped effects
- N/A axes and concrete reasons confirmed: PASS — no N/A axes claimed
- Pre-implementation findings resolved and plan re-reviewed: NO
- Verified gap: BM-05 omits missing/placeholder `AGENTS.md`, missing `CONTRACTS.md`, missing registered module path, and missing registered contract-doc failure mappings through `main(repo_root)`
- Verified gap: BM-02 omits contract-only and contract+registry-row-without-implementation-marker activation states for both future owners
- Verified gap: BM-04 allocates only missing-reference drift; extra, reordered, and altered reference-clause failures lack deterministic test identifiers
- Verified gap: BM-01 omits the wrong-owner test for `0013-workshop-mvp-boundary`
- Late-gap rule acknowledged: YES
<!-- matrix-attestation:end -->

## Red-test evidence before production code

- Commands: `cd backend && .venv/bin/pytest -q tests/test_hub.py` and `make hub-check`.
- Expected failures: the focused tests fail because ADR-0013–0017, the frozen tenant inventories,
  `check_workshop_charter`, future-owner declarations, the readiness overlay, and
  `workshop/README.md` do not exist; partial activation fixtures reach deterministic exit 1. The
  unchanged baseline `make hub-check` still passes and is recorded as characterization evidence.
- Observed failures: not run because implementation has not started.
- Characterization exception: none; these are deterministic hub contracts.
- LLM integration omission: no model surface is changed.

## Implementation sequence

1. Add the named `backend/tests/test_hub.py` nodes plus `FUTURE_WORKSHOP_OWNERS`,
   `WORKSHOP_TENANT_KEY_GROUPS`, `WORKSHOP_REFERENCE_GROUPS`, and
   `check_workshop_charter(repo_root)` in `scripts/hub_check.py`; thread `repo_root` through
   `check_agents_md_placeholders`, `check_contracts_table`, and `main`. Capture focused red output
   plus baseline `make hub-check` pass before document edits.
2. Add and accept ADR-0013 through ADR-0017 with the decision/tenant-key allocation above; do not
   create, rename, or edit any ADR-0009 path.
3. Update `docs/system_contract.md`, `docs/module_contracts/README.md`, and prose outside the parsed
   table in `CONTRACTS.md` to declare future owners and the same-PR activation rule. Preserve every
   current live table row; do not add future module contract files or registry rows in S1.
4. Add the R1 workshop overlay to `backlog/pi/10_implementation_readiness.md` without changing R2
   entry/exit criteria, and add `workshop/README.md` with the owned-synthetic-only source rule.
5. Run the focused hub tests, `make hub-check`, `make test`, and `make verify`.

## Verification and acceptance

- All named BM-01–BM-05 tests and `make hub-check` pass.
- ADR ownership and the complete candidate-key/reference rules match the immutable source snapshot.
- Current bare/scalar ORM shapes remain explicitly characterized; S1 changes no ORM or migration.
- Current live contract rows remain present, future owners remain declarations until their packages
  land, and ADR-0009 remains absent/reserved.
- No production module/package/import/route/profile gains workshop behavior and no held
  package-review or legal-attestation design changes.
- `make verify` passes before merge.
