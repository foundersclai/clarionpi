# ADR-0013 — Workshop MVP boundary and tenant-key discipline

Status: accepted · Date: 2026-07-18 · Source: Workshop MVP plan set (WMVP-00/S1)

Decision owner: Workshop MVP boundary and tenant-key discipline.

## Context

The Workshop MVP needs a demonstration surface that proves the ordinary ClarionPI workflow on
owned synthetic scenarios. It must not be mistaken for the legal, privacy, operational, or
live-matter evidence required for the captive-firm pilot. The plan set also introduces tables and
references whose tenant keys must be fixed before downstream migrations can safely land.

The live ORM predates this plan and has scalar parent references. This ADR freezes the target
contract but does not repair the schema. Migration slices remain responsible for candidate keys,
compound references, preflights, and backend parity.

## Decision 1 — Workshop is a disclosed R1 overlay

Workshop scenarios use the standard domain services, attorney gates, registry, money engine,
validators, package builder, artifact store, and provenance routes. Workshop-specific control is
limited to runtime/scenario lifecycle, disclosure, capability policy, and artifact distribution.
There is no alternate transition table, auto-approval, guard weakening, downstream-row seed, or
Workshop-specific legal conclusion.

Only owned-synthetic inputs may be used. Demo identity is permanent, production refuses demo
providers/loaders independently of the frontend, and Workshop evidence cannot satisfy R2 legal,
PHI, ethics, or live-pilot evidence requirements.

ADR-0009 remains absent and reserved. ADR-0013 through ADR-0017 are allocated in order to the
Workshop boundary and four shared prerequisite decisions. The shared decisions stay in their own
ADRs so later non-Workshop consumers can depend on them without importing Workshop policy.

## Decision 2 — future ownership is declaration-first and activation-atomic

`app.core.matter_access` will own the persisted capability single door, with implementation marker
`backend/app/core/matter_access.py`, contract
`docs/module_contracts/app.core.matter_access.md`, and registry path `backend/app/core`.
`app.workshop.lifecycle` will own scenario/workspace lifecycle, with implementation marker
`backend/app/workshop/lifecycle.py`, contract
`docs/module_contracts/app.workshop.lifecycle.md`, and registry path `backend/app/workshop`.

S1 declares these owners outside the live `CONTRACTS.md` table. A declaration with no activation
artifacts is valid. Later activation is valid only when the implementation marker, contract, and
live registry row land in the same PR; every partial state fails the hub gate.

## Decision 3 — full tenant scope is part of identity

Every new reference uses the full applicable candidate key. The following rules are normative:

Cross-firm reference mixtures are forbidden.

Same-firm/different-matter reference mixtures are forbidden.

Columns from different parent rows may not be mixed.

A bare UUID is not a tenant-safe foreign-key substitute.

A bare matter-local integer version is a fence, not a foreign-key substitute.

Nullable compound references are all-null or all-present.

Use PostgreSQL MATCH FULL where supported.

Preflight duplicate parent candidate keys.

Preflight and refuse cross-scope reference data.

Preflight and refuse partially-null compound references.

WMVP-01D must land the RegistryVersion candidate key before its first intermediate head.

SQLite checks and application validation must enforce the all-null/all-present rule where backend
syntax differs. Preflights run before a constraint lands and refuse bad legacy rows rather than
silently rewriting identity.

## Frozen candidate-key inventory

The group names, entry order, model names, and column order below are exact. The adjacent stdlib
hub constants mirror this block and fail on a missing, extra, reordered, or altered entry.

<!-- workshop-tenant-keys:start -->
### tenant-roots-decision
- `Matter(firm_id,id)`
- `Matter(firm_id,id,matter_purpose)`
- `Matter(firm_id,id,matter_purpose,demo_scenario_id,demo_scenario_version,demo_label_version)`
- `User(firm_id,id)`
- `GateRecord(firm_id,matter_id,id)`
- `GateRecord(firm_id,matter_id,id,evidence_binding_state,result_evidence_version,result_evidence_head_sha256,result_analysis_binding_state,result_analysis_generation_id,result_evidence_registry_version)`
- `GateRecord(firm_id,matter_id,id,result_binding_state,result_draft_id,result_draft_version,compliance_head_sha256)`
### strategy-settlement
- `StrategyInputs(firm_id,matter_id,id,version)`
- `RequestedDemandElection(firm_id,matter_id,id,version)`
- `StrategyPlan(firm_id,matter_id,id,version)`
- `StrategyPlan(firm_id,matter_id,id,version,source_operation_run_id)`
### generation-publication
- `CorpusVersion(firm_id,matter_id,version)`
- `EvidenceVersion(firm_id,matter_id,version)`
- `EvidenceVersion(firm_id,matter_id,version,head_sha256,analysis_binding_state,analysis_generation_id)`
- `RegistryVersion(firm_id,matter_id,version)`
- `RegistryVersion(firm_id,matter_id,version,source_operation_run_id)`
- `AnalysisGeneration(firm_id,matter_id,id)`
- `AnalysisGeneration(firm_id,matter_id,id,result_registry_version)`
- `AnalysisGeneration(firm_id,matter_id,id,analysis_operation_run_id,result_registry_version)`
### analysis-facts
- `MedicalEncounter(firm_id,matter_id,id)`
- `EncounterNarrative(firm_id,matter_id,encounter_id,analysis_generation_id)`
- `RiskFlag(firm_id,matter_id,id)`
### draft-compliance
- `DemandDraft(firm_id,matter_id,id,version)`
- `DemandDraft(firm_id,matter_id,version)`
- `DemandDraft(firm_id,matter_id,id,version,owning_operation_run_id)`
- `ComplianceFinding(firm_id,matter_id,id)`
- `ComplianceFindingRevision(firm_id,matter_id,finding_id,revision)`
- `ComplianceFindingRevision(firm_id,matter_id,draft_id,draft_version,finding_id,revision)`
- `ComplianceFindingRevision(firm_id,matter_id,draft_id,draft_version,finding_id,revision,source_operation_run_id)`
### operations-telemetry
- `MatterOperationRun(firm_id,matter_id,id)`
- `MatterBudget(firm_id,matter_id)`
- `AuditEvent(firm_id,id)`
- `ProviderInvocation(firm_id,matter_id,attempt_id)`
- `LlmCall(firm_id,matter_id,attempt_id)`
### ingest
- `CaseDocument(firm_id,matter_id,id)`
- `Phase0RunDocument(firm_id,matter_id,run_id,document_id)`
- `UploadSession(firm_id,matter_id,id)`
- `UploadSlot(firm_id,matter_id,session_id,id)`
- `UploadBlobAttempt(firm_id,matter_id,session_id,slot_id,id)`
### workshop-authority
- `WorkshopEvidenceRun(firm_id,id)`
- `WorkshopEvidenceRun(firm_id,matter_id,id)`
- `WorkshopScenarioSeal(firm_id,matter_id)`
- `WorkshopScenarioSeal(firm_id,matter_id,id)`
### artifacts
- `ArtifactReuseRecord(firm_id,matter_id,id,operation_run_id,artifact_set_id)`
- `ArtifactSet(firm_id,matter_id,id)`
- `ArtifactSet(firm_id,matter_id,id,operation_run_id)`
- `ArtifactPublication(firm_id,matter_id,id)`
- `ArtifactPublication(firm_id,matter_id,id,state)`
<!-- workshop-tenant-keys:end -->

## Frozen reference inventory

These 21 clauses are likewise exact. Arrows describe the child-to-parent relationship; later ADRs
and migration slices provide table-specific constraint names and sequencing.

<!-- workshop-references:start -->
### strategy-authority
- `strategy revision -> actor and gate`
- `election -> revision`
- `plan and token -> election`
- `plan -> exact composite G2a evidence result`
- `draft -> approved plan`
- `gate result -> plan or draft`
### budget-run
- `budget -> Matter`
- `warning audit -> AuditEvent`
- `resumed run, LlmCall, and PlanEmitAttempt -> run`
- `workshop UploadSession, operation, ProviderInvocation, GateRecord, ArtifactSet, CorpusVersion, and EvidenceVersion -> evidence run`
### corpus-evidence
- `corpus head and processed pointers -> corpus version`
- `evidence head and G2a result -> evidence version`
- `Phase0 membership -> CaseDocument`
### analysis-current
- `analysis narrative -> MedicalEncounter and generation`
- `analysis child and current pointer -> generation`
- `carried risk flag -> prior same-matter flag`
- `Matter -> current draft`
### upload-publication
- `seal -> active and sealed session`
- `slot and session -> blob attempt`
- `run -> typed result plus its producing run or an explicit typed preexisting/reuse result record`
- `ArtifactSet and publication -> draft, G3 GateRecord, run, and publication`
<!-- workshop-references:end -->

## Legacy characterization and migration ownership

Legacy present model families: 17.

Legacy planned model families absent: 14.

Legacy relationships use scalar or bare parent keys.

Legacy matter-local unique shapes remain characterized.

Legacy integer versions are fences, not foreign keys.

S1 does not add ORM constraints or migrations. Downstream migration slices own schema changes and
must preserve deployable intermediate heads. In particular, WMVP-01D preflights duplicate
`RegistryVersion(firm_id,matter_id,version)` identities, creates that candidate key, and binds its
Phase-0 result/progress references before WMVP-01G-b consumes it for analysis results and
`StrategyPlan.evidence_registry_version`.

## Consequences

- Reviewers can distinguish Workshop product evidence from R2 release evidence.
- Future-owner declarations do not lie about packages or contracts that are not present.
- The exact tenant/reference inventory becomes a visible repository drift gate.
- Every downstream migration must prove cross-firm, same-firm/different-matter, mixed-column,
  partial-null, duplicate-parent, and intermediate-head failure behavior.
- Existing ORM and Alembic files remain unchanged in S1.
