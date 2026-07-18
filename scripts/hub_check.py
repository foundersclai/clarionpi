#!/usr/bin/env python3
"""Drift gate between ClarionPI's repository contracts and the live tree.

The gate is deliberately stdlib-only so it can run before the backend environment exists. It
checks the root hub documents, the live module-contract registry, and the accepted Workshop MVP
charter. Future Workshop owners remain declarations until marker, contract, and registry row land
together.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")
TABLE_ROW_RE = re.compile(r"^\|(?P<cells>.+)\|\s*$")

WORKSHOP_ADRS = (
    (
        "0013",
        "workshop-mvp-boundary",
        "Decision owner: Workshop MVP boundary and tenant-key discipline.",
    ),
    (
        "0014",
        "requested-demand-settlement",
        "Decision owner: Requested demand and settlement.",
    ),
    (
        "0015",
        "operation-and-generation-publication",
        "Decision owner: Operation ownership and generation publication.",
    ),
    (
        "0016",
        "draft-compliance-authority",
        "Decision owner: Draft and compliance authority.",
    ),
    (
        "0017",
        "artifact-publication-recovery",
        "Decision owner: Artifact publication and recovery.",
    ),
)

WORKSHOP_ADR_DEPENDENCIES = (
    ("0014", "Dependency: accept ADR-0014 before settlement implementation."),
    ("0015", "Dependency: accept ADR-0015 before operation/generation work."),
    ("0016", "Dependency: accept ADR-0016 before draft/compliance work."),
    ("0017", "Dependency: accept ADR-0017 before publication work."),
)

FUTURE_WORKSHOP_OWNERS = (
    (
        "app.core.matter_access",
        "backend/app/core/matter_access.py",
        "docs/module_contracts/app.core.matter_access.md",
        "backend/app/core",
    ),
    (
        "app.workshop.lifecycle",
        "backend/app/workshop/lifecycle.py",
        "docs/module_contracts/app.workshop.lifecycle.md",
        "backend/app/workshop",
    ),
)

WORKSHOP_TENANT_KEY_GROUPS = (
    (
        "tenant-roots-decision",
        (
            "Matter(firm_id,id)",
            "Matter(firm_id,id,matter_purpose)",
            "Matter(firm_id,id,matter_purpose,demo_scenario_id,demo_scenario_version,"
            "demo_label_version)",
            "User(firm_id,id)",
            "GateRecord(firm_id,matter_id,id)",
            "GateRecord(firm_id,matter_id,id,evidence_binding_state,result_evidence_version,"
            "result_evidence_head_sha256,result_analysis_binding_state,"
            "result_analysis_generation_id,result_evidence_registry_version)",
            "GateRecord(firm_id,matter_id,id,result_binding_state,result_draft_id,"
            "result_draft_version,compliance_head_sha256)",
        ),
    ),
    (
        "strategy-settlement",
        (
            "StrategyInputs(firm_id,matter_id,id,version)",
            "RequestedDemandElection(firm_id,matter_id,id,version)",
            "StrategyPlan(firm_id,matter_id,id,version)",
            "StrategyPlan(firm_id,matter_id,id,version,source_operation_run_id)",
        ),
    ),
    (
        "generation-publication",
        (
            "CorpusVersion(firm_id,matter_id,version)",
            "EvidenceVersion(firm_id,matter_id,version)",
            "EvidenceVersion(firm_id,matter_id,version,head_sha256,analysis_binding_state,"
            "analysis_generation_id)",
            "RegistryVersion(firm_id,matter_id,version)",
            "RegistryVersion(firm_id,matter_id,version,source_operation_run_id)",
            "AnalysisGeneration(firm_id,matter_id,id)",
            "AnalysisGeneration(firm_id,matter_id,id,result_registry_version)",
            "AnalysisGeneration(firm_id,matter_id,id,analysis_operation_run_id,"
            "result_registry_version)",
        ),
    ),
    (
        "analysis-facts",
        (
            "MedicalEncounter(firm_id,matter_id,id)",
            "EncounterNarrative(firm_id,matter_id,encounter_id,analysis_generation_id)",
            "RiskFlag(firm_id,matter_id,id)",
        ),
    ),
    (
        "draft-compliance",
        (
            "DemandDraft(firm_id,matter_id,id,version)",
            "DemandDraft(firm_id,matter_id,version)",
            "DemandDraft(firm_id,matter_id,id,version,owning_operation_run_id)",
            "ComplianceFinding(firm_id,matter_id,id)",
            "ComplianceFindingRevision(firm_id,matter_id,finding_id,revision)",
            "ComplianceFindingRevision(firm_id,matter_id,draft_id,draft_version,finding_id,"
            "revision)",
            "ComplianceFindingRevision(firm_id,matter_id,draft_id,draft_version,finding_id,"
            "revision,source_operation_run_id)",
        ),
    ),
    (
        "operations-telemetry",
        (
            "MatterOperationRun(firm_id,matter_id,id)",
            "MatterBudget(firm_id,matter_id)",
            "AuditEvent(firm_id,id)",
            "ProviderInvocation(firm_id,matter_id,attempt_id)",
            "LlmCall(firm_id,matter_id,attempt_id)",
        ),
    ),
    (
        "ingest",
        (
            "CaseDocument(firm_id,matter_id,id)",
            "Phase0RunDocument(firm_id,matter_id,run_id,document_id)",
            "UploadSession(firm_id,matter_id,id)",
            "UploadSlot(firm_id,matter_id,session_id,id)",
            "UploadBlobAttempt(firm_id,matter_id,session_id,slot_id,id)",
        ),
    ),
    (
        "workshop-authority",
        (
            "WorkshopEvidenceRun(firm_id,id)",
            "WorkshopEvidenceRun(firm_id,matter_id,id)",
            "WorkshopScenarioSeal(firm_id,matter_id)",
            "WorkshopScenarioSeal(firm_id,matter_id,id)",
        ),
    ),
    (
        "artifacts",
        (
            "ArtifactReuseRecord(firm_id,matter_id,id,operation_run_id,artifact_set_id)",
            "ArtifactSet(firm_id,matter_id,id)",
            "ArtifactSet(firm_id,matter_id,id,operation_run_id)",
            "ArtifactPublication(firm_id,matter_id,id)",
            "ArtifactPublication(firm_id,matter_id,id,state)",
        ),
    ),
)

WORKSHOP_REFERENCE_GROUPS = (
    (
        "strategy-authority",
        (
            "strategy revision -> actor and gate",
            "election -> revision",
            "plan and token -> election",
            "plan -> exact composite G2a evidence result",
            "draft -> approved plan",
            "gate result -> plan or draft",
        ),
    ),
    (
        "budget-run",
        (
            "budget -> Matter",
            "warning audit -> AuditEvent",
            "resumed run, LlmCall, and PlanEmitAttempt -> run",
            "workshop UploadSession, operation, ProviderInvocation, GateRecord, ArtifactSet, "
            "CorpusVersion, and EvidenceVersion -> evidence run",
        ),
    ),
    (
        "corpus-evidence",
        (
            "corpus head and processed pointers -> corpus version",
            "evidence head and G2a result -> evidence version",
            "Phase0 membership -> CaseDocument",
        ),
    ),
    (
        "analysis-current",
        (
            "analysis narrative -> MedicalEncounter and generation",
            "analysis child and current pointer -> generation",
            "carried risk flag -> prior same-matter flag",
            "Matter -> current draft",
        ),
    ),
    (
        "upload-publication",
        (
            "seal -> active and sealed session",
            "slot and session -> blob attempt",
            "run -> typed result plus its producing run or an explicit typed preexisting/reuse "
            "result record",
            "ArtifactSet and publication -> draft, G3 GateRecord, run, and publication",
        ),
    ),
)

R2_RELEASE_ROW = (
    "| **R2 — captive-firm pilot** | First real matters, founder-supervised | "
    "B1+B2+B3+B4 closed; ABS licensed; [12](./12_abs_ops_runbook.md) adopted by counsel | "
    "First real demand shipped w/ zero unanchored facts; runbook cadences running |"
)

TENANT_SCOPE_RULES = (
    "Cross-firm reference mixtures are forbidden.",
    "Same-firm/different-matter reference mixtures are forbidden.",
    "Columns from different parent rows may not be mixed.",
)

BARE_KEY_RULES = (
    "A bare UUID is not a tenant-safe foreign-key substitute.",
    "A bare matter-local integer version is a fence, not a foreign-key substitute.",
)

TENANT_MIGRATION_OBLIGATIONS = (
    "Nullable compound references are all-null or all-present.",
    "Use PostgreSQL MATCH FULL where supported.",
    "Preflight duplicate parent candidate keys.",
    "Preflight and refuse cross-scope reference data.",
    "Preflight and refuse partially-null compound references.",
    "WMVP-01D must land the RegistryVersion candidate key before its first intermediate head.",
)

LEGACY_ORM_CHARACTERIZATION = (
    "Legacy present model families: 17.",
    "Legacy planned model families absent: 14.",
    "Legacy relationships use scalar or bare parent keys.",
    "Legacy matter-local unique shapes remain characterized.",
    "Legacy integer versions are fences, not foreign keys.",
)


def check_agents_md_placeholders(repo_root: Path = REPO_ROOT) -> list[str]:
    agents_md = repo_root / "AGENTS.md"
    if not agents_md.exists():
        return [f"hub-check: FAIL — {agents_md} does not exist"]

    errors: list[str] = []
    text = agents_md.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in PLACEHOLDER_RE.finditer(line):
            errors.append(
                f"hub-check: FAIL — AGENTS.md:{lineno} has unfilled placeholder "
                f"{match.group(0)!r}"
            )
    return errors


def parse_contracts_table(text: str) -> list[tuple[str, str, str]]:
    """Return ``(module_path, contract_doc, notes)`` rows from the registry table."""
    rows: list[tuple[str, str, str]] = []
    seen_header = False
    for line in text.splitlines():
        match = TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        cells = [cell.strip() for cell in match.group("cells").split("|")]
        if len(cells) < 2:
            continue
        first_cell = cells[0]
        if not seen_header:
            seen_header = True
            continue
        if set(first_cell) <= {"-", ":"} and first_cell:
            continue
        if not first_cell:
            continue
        contract_doc = cells[1] if len(cells) > 1 else ""
        notes = cells[2] if len(cells) > 2 else ""
        rows.append((first_cell, contract_doc, notes))
    return rows


def check_contracts_table(repo_root: Path = REPO_ROOT) -> tuple[list[str], int]:
    contracts_md = repo_root / "CONTRACTS.md"
    if not contracts_md.exists():
        return ([f"hub-check: FAIL — {contracts_md} does not exist"], 0)

    errors: list[str] = []
    rows = parse_contracts_table(contracts_md.read_text(encoding="utf-8"))
    for module_path, contract_doc, _notes in rows:
        if not (repo_root / module_path).exists():
            errors.append(
                "hub-check: FAIL — CONTRACTS.md lists module path "
                f"{module_path!r} which does not exist"
            )
        if not (repo_root / contract_doc).exists():
            errors.append(
                "hub-check: FAIL — CONTRACTS.md lists contract doc "
                f"{contract_doc!r} which does not exist"
            )
    return (errors, len(rows))


def _future_owner_line(owner: tuple[str, str, str, str]) -> str:
    name, marker, contract, registry_path = owner
    return (
        f"- `{name}` — future owner; implementation marker `{marker}`; contract `{contract}`; "
        f"live registry path `{registry_path}`; activate marker, contract, and row in one PR."
    )


def _parse_group_block(text: str, marker: str) -> tuple[tuple[str, tuple[str, ...]], ...] | None:
    start = f"<!-- {marker}:start -->"
    end = f"<!-- {marker}:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        return None
    body = text.split(start, 1)[1].split(end, 1)[0]
    groups: list[tuple[str, tuple[str, ...]]] = []
    current_name: str | None = None
    current_entries: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            if current_name is not None:
                groups.append((current_name, tuple(current_entries)))
            current_name = line.removeprefix("### ").strip()
            current_entries = []
        elif line.startswith("- `") and line.endswith("`") and current_name is not None:
            current_entries.append(line[3:-1])
    if current_name is not None:
        groups.append((current_name, tuple(current_entries)))
    return tuple(groups)


def _check_workshop_adrs(repo_root: Path) -> list[str]:
    errors: list[str] = []
    texts: dict[str, str] = {}
    for adr_id, slug, owner in WORKSHOP_ADRS:
        path = repo_root / f"docs/adr/{adr_id}-{slug}.md"
        if not path.exists():
            errors.append(f"hub-check: FAIL — workshop charter: ADR-{adr_id} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        texts[adr_id] = text
        if owner not in text:
            errors.append(
                f"hub-check: FAIL — workshop charter: ADR-{adr_id} decision owner is wrong"
            )

    for adr_id, dependency in WORKSHOP_ADR_DEPENDENCIES:
        if adr_id in texts and dependency not in texts[adr_id]:
            errors.append(
                f"hub-check: FAIL — workshop charter: ADR-{adr_id} dependency is missing"
            )

    if list((repo_root / "docs/adr").glob("0009*.md")):
        errors.append("hub-check: FAIL — workshop charter: ADR-0009 must remain absent/reserved")

    boundary = texts.get("0013")
    if boundary is None:
        return errors
    for adr_id, _slug, owner in WORKSHOP_ADRS[1:]:
        if owner in boundary:
            errors.append(
                "hub-check: FAIL — workshop charter: ADR-0013 folds a shared decision "
                f"owned by ADR-{adr_id}"
            )

    if (repo_root / "docs/legal_attestation.md").exists() or "Legal attestation:" in boundary:
        errors.append("hub-check: FAIL — workshop charter: S1 must not add a legal attestation")

    for rule in TENANT_SCOPE_RULES:
        if rule not in boundary:
            errors.append(
                "hub-check: FAIL — workshop charter: ADR-0013 tenant scope rule is missing: "
                f"{rule}"
            )
    for rule in BARE_KEY_RULES:
        if rule not in boundary:
            errors.append(
                "hub-check: FAIL — workshop charter: ADR-0013 bare-key rule is missing: "
                f"{rule}"
            )
    for obligation in TENANT_MIGRATION_OBLIGATIONS:
        if obligation not in boundary:
            errors.append(
                "hub-check: FAIL — workshop charter: ADR-0013 migration obligation is missing: "
                f"{obligation}"
            )
    for characterization in LEGACY_ORM_CHARACTERIZATION:
        if characterization not in boundary:
            errors.append(
                "hub-check: FAIL — workshop charter: ADR-0013 legacy ORM characterization "
                f"is missing: {characterization}"
            )

    candidate_groups = _parse_group_block(boundary, "workshop-tenant-keys")
    reference_groups = _parse_group_block(boundary, "workshop-references")
    if candidate_groups != WORKSHOP_TENANT_KEY_GROUPS:
        errors.append(
            "hub-check: FAIL — workshop charter: ADR-0013 tenant inventory candidate-key "
            "groups do not match the frozen ordered contract"
        )
    if reference_groups != WORKSHOP_REFERENCE_GROUPS:
        errors.append(
            "hub-check: FAIL — workshop charter: ADR-0013 tenant inventory reference groups "
            "do not match the frozen ordered contract"
        )
    return errors


def _check_future_owners(repo_root: Path) -> list[str]:
    errors: list[str] = []
    declaration_paths = (
        "docs/system_contract.md",
        "docs/module_contracts/README.md",
        "CONTRACTS.md",
    )
    declarations = {
        path: (repo_root / path).read_text(encoding="utf-8")
        if (repo_root / path).exists()
        else ""
        for path in declaration_paths
    }
    contracts_text = declarations["CONTRACTS.md"]
    rows = parse_contracts_table(contracts_text) if contracts_text else []

    for owner in FUTURE_WORKSHOP_OWNERS:
        name, marker, contract, registry_path = owner
        line = _future_owner_line(owner)
        for declaration_path, text in declarations.items():
            if line not in text:
                errors.append(
                    "hub-check: FAIL — workshop charter: "
                    f"{declaration_path} missing future owner {name}"
                )

        marker_present = (repo_root / marker).exists()
        contract_present = (repo_root / contract).exists()
        row_present = any(
            row_module == registry_path and row_contract == contract
            for row_module, row_contract, _notes in rows
        )
        if (marker_present, contract_present, row_present) not in {
            (False, False, False),
            (True, True, True),
        }:
            errors.append(
                f"hub-check: FAIL — workshop charter: {name} activation is partial "
                f"(marker={marker_present}, contract={contract_present}, row={row_present})"
            )
    return errors


def _check_readiness_and_sources(repo_root: Path) -> list[str]:
    errors: list[str] = []
    readiness_path = repo_root / "backlog/pi/10_implementation_readiness.md"
    readiness = (
        readiness_path.read_text(encoding="utf-8") if readiness_path.exists() else ""
    )
    overlay_tokens = (
        "<!-- workshop-mvp-r1-overlay:start -->",
        "### Workshop MVP R1 overlay",
        "owned-synthetic",
        "cannot\nclose the legal, PHI, ethics, or live-pilot gates for R2.",
        "<!-- workshop-mvp-r1-overlay:end -->",
    )
    if any(token not in readiness for token in overlay_tokens):
        errors.append("hub-check: FAIL — workshop charter: R1 readiness overlay is missing")
    if R2_RELEASE_ROW not in readiness:
        errors.append(
            "hub-check: FAIL — workshop charter: canonical R2 release row changed"
        )

    source_path = repo_root / "workshop/README.md"
    source = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    if "Only owned-synthetic scenario inputs are allowed." not in source:
        errors.append(
            "hub-check: FAIL — workshop charter: owned-synthetic source rule is missing"
        )
    forbidden_tokens = ("`samples/`", "tests", "real case records")
    if any(token not in source for token in forbidden_tokens):
        errors.append(
            "hub-check: FAIL — workshop charter: forbidden Workshop sources are not complete"
        )
    gate_rule = (
        "Workshop evidence cannot close the legal, PHI, ethics, or live-pilot gates for R2."
    )
    if gate_rule not in source:
        errors.append(
            "hub-check: FAIL — workshop charter: Workshop evidence cannot close R2 gates rule "
            "is missing"
        )
    return errors


def check_workshop_charter(repo_root: Path = REPO_ROOT) -> list[str]:
    """Return deterministic Workshop charter errors without mutating the repository."""
    errors: list[str] = []
    errors.extend(_check_workshop_adrs(repo_root))
    errors.extend(_check_future_owners(repo_root))
    errors.extend(_check_readiness_and_sources(repo_root))
    return errors


def main(repo_root: Path = REPO_ROOT) -> int:
    errors: list[str] = []
    errors.extend(check_agents_md_placeholders(repo_root))
    contracts_errors, module_count = check_contracts_table(repo_root)
    errors.extend(contracts_errors)
    errors.extend(check_workshop_charter(repo_root))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"hub-check: OK ({module_count} modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
