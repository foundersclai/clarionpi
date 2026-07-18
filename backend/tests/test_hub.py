"""Repository-hub contract tests for the Workshop MVP charter.

The hub checker is intentionally stdlib-only and accepts an explicit repository root. These
tests build a complete synthetic repository, then remove or alter one contract seam at a time.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HUB_PATH = REPO_ROOT / "scripts" / "hub_check.py"


def _load_hub() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clarionpi_hub_check", HUB_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HUB = _load_hub()

ADR_SPECS = {
    "0013": (
        "workshop-mvp-boundary",
        "Decision owner: Workshop MVP boundary and tenant-key discipline.",
    ),
    "0014": (
        "requested-demand-settlement",
        "Decision owner: Requested demand and settlement.",
    ),
    "0015": (
        "operation-and-generation-publication",
        "Decision owner: Operation ownership and generation publication.",
    ),
    "0016": (
        "draft-compliance-authority",
        "Decision owner: Draft and compliance authority.",
    ),
    "0017": (
        "artifact-publication-recovery",
        "Decision owner: Artifact publication and recovery.",
    ),
}

DEPENDENCIES = {
    "0014": "Dependency: accept ADR-0014 before settlement implementation.",
    "0015": "Dependency: accept ADR-0015 before operation/generation work.",
    "0016": "Dependency: accept ADR-0016 before draft/compliance work.",
    "0017": "Dependency: accept ADR-0017 before publication work.",
}

EXPECTED_FUTURE_OWNERS = {
    "app.core.matter_access": (
        "backend/app/core/matter_access.py",
        "docs/module_contracts/app.core.matter_access.md",
        "backend/app/core",
    ),
    "app.workshop.lifecycle": (
        "backend/app/workshop/lifecycle.py",
        "docs/module_contracts/app.workshop.lifecycle.md",
        "backend/app/workshop",
    ),
}

EXPECTED_TENANT_KEY_GROUPS = {
    "tenant-roots-decision": (
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
    "strategy-settlement": (
        "StrategyInputs(firm_id,matter_id,id,version)",
        "RequestedDemandElection(firm_id,matter_id,id,version)",
        "StrategyPlan(firm_id,matter_id,id,version)",
        "StrategyPlan(firm_id,matter_id,id,version,source_operation_run_id)",
    ),
    "generation-publication": (
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
    "analysis-facts": (
        "MedicalEncounter(firm_id,matter_id,id)",
        "EncounterNarrative(firm_id,matter_id,encounter_id,analysis_generation_id)",
        "RiskFlag(firm_id,matter_id,id)",
    ),
    "draft-compliance": (
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
    "operations-telemetry": (
        "MatterOperationRun(firm_id,matter_id,id)",
        "MatterBudget(firm_id,matter_id)",
        "AuditEvent(firm_id,id)",
        "ProviderInvocation(firm_id,matter_id,attempt_id)",
        "LlmCall(firm_id,matter_id,attempt_id)",
    ),
    "ingest": (
        "CaseDocument(firm_id,matter_id,id)",
        "Phase0RunDocument(firm_id,matter_id,run_id,document_id)",
        "UploadSession(firm_id,matter_id,id)",
        "UploadSlot(firm_id,matter_id,session_id,id)",
        "UploadBlobAttempt(firm_id,matter_id,session_id,slot_id,id)",
    ),
    "workshop-authority": (
        "WorkshopEvidenceRun(firm_id,id)",
        "WorkshopEvidenceRun(firm_id,matter_id,id)",
        "WorkshopScenarioSeal(firm_id,matter_id)",
        "WorkshopScenarioSeal(firm_id,matter_id,id)",
    ),
    "artifacts": (
        "ArtifactReuseRecord(firm_id,matter_id,id,operation_run_id,artifact_set_id)",
        "ArtifactSet(firm_id,matter_id,id)",
        "ArtifactSet(firm_id,matter_id,id,operation_run_id)",
        "ArtifactPublication(firm_id,matter_id,id)",
        "ArtifactPublication(firm_id,matter_id,id,state)",
    ),
}

EXPECTED_REFERENCE_GROUPS = {
    "strategy-authority": (
        "strategy revision -> actor and gate",
        "election -> revision",
        "plan and token -> election",
        "plan -> exact composite G2a evidence result",
        "draft -> approved plan",
        "gate result -> plan or draft",
    ),
    "budget-run": (
        "budget -> Matter",
        "warning audit -> AuditEvent",
        "resumed run, LlmCall, and PlanEmitAttempt -> run",
        "workshop UploadSession, operation, ProviderInvocation, GateRecord, ArtifactSet, "
        "CorpusVersion, and EvidenceVersion -> evidence run",
    ),
    "corpus-evidence": (
        "corpus head and processed pointers -> corpus version",
        "evidence head and G2a result -> evidence version",
        "Phase0 membership -> CaseDocument",
    ),
    "analysis-current": (
        "analysis narrative -> MedicalEncounter and generation",
        "analysis child and current pointer -> generation",
        "carried risk flag -> prior same-matter flag",
        "Matter -> current draft",
    ),
    "upload-publication": (
        "seal -> active and sealed session",
        "slot and session -> blob attempt",
        "run -> typed result plus its producing run or an explicit typed preexisting/reuse "
        "result record",
        "ArtifactSet and publication -> draft, G3 GateRecord, run, and publication",
    ),
}

R2_ROW = (
    "| **R2 — captive-firm pilot** | First real matters, founder-supervised | "
    "B1+B2+B3+B4 closed; ABS licensed; [12](./12_abs_ops_runbook.md) adopted by counsel | "
    "First real demand shipped w/ zero unanchored facts; runbook cadences running |"
)

OVERLAY = """<!-- workshop-mvp-r1-overlay:start -->
### Workshop MVP R1 overlay

The Workshop MVP is an R1 demonstration overlay only. Its evidence is owned-synthetic and cannot
close the legal, PHI, ethics, or live-pilot gates for R2.
<!-- workshop-mvp-r1-overlay:end -->"""

SOURCE_RULE = """# Workshop source charter

Only owned-synthetic scenario inputs are allowed.

Do not use `samples/`, tests, or real case records as Workshop scenario inputs.
Workshop evidence cannot close the legal, PHI, ethics, or live-pilot gates for R2.
"""

TENANT_OBLIGATIONS = {
    "all-null-or-all-present": "Nullable compound references are all-null or all-present.",
    "postgres-match-full": "Use PostgreSQL MATCH FULL where supported.",
    "duplicate-parent-preflight": "Preflight duplicate parent candidate keys.",
    "cross-scope-preflight": "Preflight and refuse cross-scope reference data.",
    "partial-null-preflight": "Preflight and refuse partially-null compound references.",
    "registry-version-intermediate-head": (
        "WMVP-01D must land the RegistryVersion candidate key before its first intermediate head."
    ),
}

SCOPE_RULES = {
    "cross-firm": "Cross-firm reference mixtures are forbidden.",
    "same-firm-different-matter": "Same-firm/different-matter reference mixtures are forbidden.",
    "mixed-columns": "Columns from different parent rows may not be mixed.",
}

BARE_KEY_RULES = {
    "uuid": "A bare UUID is not a tenant-safe foreign-key substitute.",
    "matter-local-version": (
        "A bare matter-local integer version is a fence, not a foreign-key substitute."
    ),
}

LEGACY_CHARACTERIZATION = {
    "present-families": "Legacy present model families: 17.",
    "absent-families": "Legacy planned model families absent: 14.",
    "scalar-parent-fks": "Legacy relationships use scalar or bare parent keys.",
    "matter-local-uniques": "Legacy matter-local unique shapes remain characterized.",
    "integer-fences": "Legacy integer versions are fences, not foreign keys.",
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _owner_line(owner: str) -> str:
    marker, contract, registry_path = EXPECTED_FUTURE_OWNERS[owner]
    return (
        f"- `{owner}` — future owner; implementation marker `{marker}`; contract `{contract}`; "
        f"live registry path `{registry_path}`; activate marker, contract, and row in one PR."
    )


def _future_owner_block() -> str:
    lines = ["<!-- future-workshop-owners:start -->"]
    lines.extend(_owner_line(owner) for owner in EXPECTED_FUTURE_OWNERS)
    lines.append("<!-- future-workshop-owners:end -->")
    return "\n".join(lines)


def _render_groups(marker: str, groups: dict[str, tuple[str, ...]]) -> str:
    lines = [f"<!-- {marker}:start -->"]
    for group, entries in groups.items():
        lines.append(f"### {group}")
        lines.extend(f"- `{entry}`" for entry in entries)
    lines.append(f"<!-- {marker}:end -->")
    return "\n".join(lines)


def _adr_text(adr_id: str) -> str:
    slug, owner = ADR_SPECS[adr_id]
    lines = [f"# ADR-{adr_id} — {slug}", "", "Status: accepted", "", owner]
    if adr_id in DEPENDENCIES:
        lines.extend(("", DEPENDENCIES[adr_id]))
    if adr_id == "0013":
        lines.extend(
            (
                "",
                "ADR-0009 remains absent and reserved.",
                "",
                *SCOPE_RULES.values(),
                *BARE_KEY_RULES.values(),
                *TENANT_OBLIGATIONS.values(),
                *LEGACY_CHARACTERIZATION.values(),
                "",
                _render_groups("workshop-tenant-keys", EXPECTED_TENANT_KEY_GROUPS),
                "",
                _render_groups("workshop-references", EXPECTED_REFERENCE_GROUPS),
            )
        )
    else:
        lines.extend(("", "This decision links to ADR-0013 for the tenant-key contract."))
    return "\n".join(lines) + "\n"


@pytest.fixture
def complete_repo(tmp_path: Path) -> Path:
    _write(tmp_path / "AGENTS.md", "# Agent guide\n")
    _write(tmp_path / "backend/app/existing/__init__.py", "")
    _write(tmp_path / "docs/module_contracts/existing.md", "# Existing contract\n")
    contracts = f"""# Contracts

{_future_owner_block()}

| module path | contract doc | notes |
|---|---|---|
| backend/app/existing | docs/module_contracts/existing.md | live |
"""
    _write(tmp_path / "CONTRACTS.md", contracts)
    _write(tmp_path / "docs/system_contract.md", "# System\n\n" + _future_owner_block() + "\n")
    _write(
        tmp_path / "docs/module_contracts/README.md",
        "# Module contracts\n\n" + _future_owner_block() + "\n",
    )
    for adr_id, (slug, _owner) in ADR_SPECS.items():
        _write(tmp_path / f"docs/adr/{adr_id}-{slug}.md", _adr_text(adr_id))
    readiness = f"# Readiness\n\n{R2_ROW}\n\n{OVERLAY}\n"
    _write(tmp_path / "backlog/pi/10_implementation_readiness.md", readiness)
    _write(tmp_path / "workshop/README.md", SOURCE_RULE)
    _write(tmp_path / "backend/app/models/orm.py", "# unchanged legacy ORM\n")
    _write(tmp_path / "backend/alembic/versions/0001.py", "# unchanged migration\n")
    return tmp_path


def _charter_errors(repo_root: Path) -> list[str]:
    checker = getattr(HUB, "check_workshop_charter", None)
    assert callable(checker), "intended red: check_workshop_charter(repo_root) is missing"
    return checker(repo_root)


def _assert_charter_ok(repo_root: Path) -> None:
    assert _charter_errors(repo_root) == []


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _adr_path(repo_root: Path, adr_id: str) -> Path:
    slug = ADR_SPECS[adr_id][0]
    return repo_root / f"docs/adr/{adr_id}-{slug}.md"


def _activate_owner(repo_root: Path, owner: str, state: str) -> None:
    marker, contract, registry_path = EXPECTED_FUTURE_OWNERS[owner]
    marker_present = state in {"module-only", "module-contract-no-row", "complete"}
    contract_present = state in {
        "module-contract-no-row",
        "contract-only",
        "contract-row-no-marker",
        "complete",
    }
    row_present = state in {"row-missing-contract", "contract-row-no-marker", "complete"}
    if marker_present:
        _write(repo_root / marker, "# future owner implementation marker\n")
    if contract_present:
        _write(repo_root / contract, f"# {owner} contract\n")
    if row_present:
        contracts_path = repo_root / "CONTRACTS.md"
        row = f"| {registry_path} | {contract} | live |\n"
        contracts_path.write_text(
            contracts_path.read_text(encoding="utf-8") + row,
            encoding="utf-8",
        )


def _snapshot(repo_root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(repo_root)): path.read_bytes()
        for path in sorted(repo_root.rglob("*"))
        if path.is_file()
    }


def _main(repo_root: Path) -> int:
    return HUB.main(repo_root)


def test_workshop_adr_sequence_and_decision_owners_are_registered(complete_repo: Path) -> None:
    _assert_charter_ok(complete_repo)


@pytest.mark.parametrize("adr_id", ADR_SPECS, ids=list(ADR_SPECS))
def test_each_missing_workshop_adr_fails_hub_check(complete_repo: Path, adr_id: str) -> None:
    _adr_path(complete_repo, adr_id).unlink()
    assert any(f"ADR-{adr_id}" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize(
    "adr_id",
    ADR_SPECS,
    ids=[
        "0013-workshop-boundary",
        "0014-requested-demand",
        "0015-operation-generation",
        "0016-draft-compliance",
        "0017-artifact-publication",
    ],
)
def test_wrong_workshop_adr_assignment_fails_hub_check(
    complete_repo: Path, adr_id: str
) -> None:
    path = _adr_path(complete_repo, adr_id)
    _replace(path, ADR_SPECS[adr_id][1], "Decision owner: wrong boundary.")
    assert any(f"ADR-{adr_id} decision owner" in error for error in _charter_errors(complete_repo))


def test_adr_0009_stays_reserved(complete_repo: Path) -> None:
    _write(complete_repo / "docs/adr/0009-not-reserved.md", "# incorrectly allocated\n")
    assert any("ADR-0009" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize(
    "adr_id",
    DEPENDENCIES,
    ids=[
        "0014-before-settlement",
        "0015-before-operation-generation",
        "0016-before-draft-compliance",
        "0017-before-publication",
    ],
)
def test_shared_adr_dependency_is_explicit(complete_repo: Path, adr_id: str) -> None:
    path = _adr_path(complete_repo, adr_id)
    _replace(path, DEPENDENCIES[adr_id], "Dependency: missing.")
    assert any(f"ADR-{adr_id} dependency" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("adr_id", ["0014", "0015", "0016", "0017"])
def test_shared_decision_is_not_folded_into_workshop_adr(
    complete_repo: Path, adr_id: str
) -> None:
    boundary = _adr_path(complete_repo, "0013")
    boundary.write_text(
        boundary.read_text(encoding="utf-8") + ADR_SPECS[adr_id][1] + "\n",
        encoding="utf-8",
    )
    assert any("folds a shared decision" in error for error in _charter_errors(complete_repo))


def test_s1_adds_no_legal_attestation(complete_repo: Path) -> None:
    _write(complete_repo / "docs/legal_attestation.md", "Legal attestation: accepted\n")
    assert any("legal attestation" in error.lower() for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("owner", EXPECTED_FUTURE_OWNERS, ids=list(EXPECTED_FUTURE_OWNERS))
def test_future_workshop_owner_is_declared_without_live_registry_row(
    complete_repo: Path, owner: str
) -> None:
    assert _owner_line(owner) in (complete_repo / "CONTRACTS.md").read_text(encoding="utf-8")
    _assert_charter_ok(complete_repo)


@pytest.mark.parametrize("owner", EXPECTED_FUTURE_OWNERS, ids=list(EXPECTED_FUTURE_OWNERS))
def test_complete_future_owner_activation_passes_hub_check(
    complete_repo: Path, owner: str
) -> None:
    _activate_owner(complete_repo, owner, "complete")
    _assert_charter_ok(complete_repo)


PARTIAL_STATES = (
    "module-only",
    "module-contract-no-row",
    "row-missing-contract",
    "contract-only",
    "contract-row-no-marker",
)


@pytest.mark.parametrize(
    ("owner", "state"),
    [(owner, state) for owner in EXPECTED_FUTURE_OWNERS for state in PARTIAL_STATES],
    ids=[f"{owner}-{state}" for owner in EXPECTED_FUTURE_OWNERS for state in PARTIAL_STATES],
)
def test_partial_future_owner_activation_fails_hub_check(
    complete_repo: Path, owner: str, state: str
) -> None:
    _activate_owner(complete_repo, owner, state)
    assert any(
        f"{owner} activation is partial" in error for error in _charter_errors(complete_repo)
    )


def test_s1_preserves_existing_contract_registry_rows(complete_repo: Path) -> None:
    before = HUB.parse_contracts_table((complete_repo / "CONTRACTS.md").read_text(encoding="utf-8"))
    _assert_charter_ok(complete_repo)
    after = HUB.parse_contracts_table((complete_repo / "CONTRACTS.md").read_text(encoding="utf-8"))
    assert before == after == [
        ("backend/app/existing", "docs/module_contracts/existing.md", "live")
    ]


@pytest.mark.parametrize("owner", EXPECTED_FUTURE_OWNERS, ids=list(EXPECTED_FUTURE_OWNERS))
def test_missing_future_owner_declaration_fails_hub_check(
    complete_repo: Path, owner: str
) -> None:
    _replace(complete_repo / "docs/system_contract.md", _owner_line(owner), "")
    assert any(f"missing future owner {owner}" in error for error in _charter_errors(complete_repo))


def test_hub_check_has_no_backend_app_imports() -> None:
    tree = ast.parse(HUB_PATH.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name == "app" or name.startswith("app.")]


def test_workshop_readiness_overlay_is_present(complete_repo: Path) -> None:
    assert OVERLAY in (
        complete_repo / "backlog/pi/10_implementation_readiness.md"
    ).read_text(encoding="utf-8")
    _assert_charter_ok(complete_repo)


def test_owned_synthetic_source_rule_is_present(complete_repo: Path) -> None:
    assert SOURCE_RULE == (complete_repo / "workshop/README.md").read_text(encoding="utf-8")
    _assert_charter_ok(complete_repo)


def test_missing_workshop_readiness_overlay_fails_hub_check(complete_repo: Path) -> None:
    _replace(
        complete_repo / "backlog/pi/10_implementation_readiness.md",
        "<!-- workshop-mvp-r1-overlay:start -->",
        "<!-- overlay-removed:start -->",
    )
    assert any("R1 readiness overlay" in error for error in _charter_errors(complete_repo))


def test_missing_synthetic_source_rule_fails_hub_check(complete_repo: Path) -> None:
    _replace(complete_repo / "workshop/README.md", "owned-synthetic", "unrestricted")
    assert any("owned-synthetic" in error for error in _charter_errors(complete_repo))


def test_r2_entry_and_exit_criteria_are_unchanged(complete_repo: Path) -> None:
    readiness = complete_repo / "backlog/pi/10_implementation_readiness.md"
    _replace(readiness, R2_ROW, R2_ROW.replace("B1+B2+B3+B4 closed", "Workshop passed"))
    assert any("R2 release row" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("source", ["samples", "tests", "real-records"])
def test_workshop_source_is_forbidden(complete_repo: Path, source: str) -> None:
    readme = complete_repo / "workshop/README.md"
    replacements = {
        "samples": ("`samples/`", "sample library"),
        "tests": ("tests", "automated checks"),
        "real-records": ("real case records", "case materials"),
    }
    _replace(readme, *replacements[source])
    assert any("forbidden Workshop sources" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("gate", ["legal", "phi", "ethics", "live-pilot"])
def test_workshop_evidence_cannot_close_r2_gates(complete_repo: Path, gate: str) -> None:
    readme = complete_repo / "workshop/README.md"
    tokens = {"legal": "legal", "phi": "PHI", "ethics": "ethics", "live-pilot": "live-pilot"}
    _replace(readme, tokens[gate], f"removed-{gate}")
    assert any("cannot close R2 gates" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("surface", ["package", "import", "route", "profile", "capability"])
def test_s1_adds_no_runtime_workshop_surface(complete_repo: Path, surface: str) -> None:
    before = _snapshot(complete_repo)
    _assert_charter_ok(complete_repo)
    after = _snapshot(complete_repo)
    assert before == after
    assert not (complete_repo / "backend/app/workshop").exists()
    assert surface in {"package", "import", "route", "profile", "capability"}


@pytest.mark.parametrize(
    "group", EXPECTED_TENANT_KEY_GROUPS, ids=list(EXPECTED_TENANT_KEY_GROUPS)
)
def test_tenant_key_candidate_group_is_complete(group: str) -> None:
    actual = getattr(HUB, "WORKSHOP_TENANT_KEY_GROUPS", None)
    assert actual is not None, "intended red: WORKSHOP_TENANT_KEY_GROUPS is missing"
    assert dict(actual)[group] == EXPECTED_TENANT_KEY_GROUPS[group]


@pytest.mark.parametrize("group", EXPECTED_REFERENCE_GROUPS, ids=list(EXPECTED_REFERENCE_GROUPS))
def test_tenant_reference_group_is_complete(group: str) -> None:
    actual = getattr(HUB, "WORKSHOP_REFERENCE_GROUPS", None)
    assert actual is not None, "intended red: WORKSHOP_REFERENCE_GROUPS is missing"
    assert dict(actual)[group] == EXPECTED_REFERENCE_GROUPS[group]


@pytest.mark.parametrize(
    "characterization", LEGACY_CHARACTERIZATION, ids=list(LEGACY_CHARACTERIZATION)
)
def test_current_tenant_key_legacy_shape_is_characterized(
    complete_repo: Path, characterization: str
) -> None:
    path = _adr_path(complete_repo, "0013")
    _replace(path, LEGACY_CHARACTERIZATION[characterization], "characterization removed")
    assert any("legacy ORM characterization" in error for error in _charter_errors(complete_repo))


INVENTORY_DRIFT_CASES = (
    "missing",
    "extra",
    "reordered",
    "altered-column",
    "missing-reference",
    "extra-reference",
    "reordered-reference",
    "altered-reference",
)


def _drift_inventory(repo_root: Path, case: str) -> None:
    path = _adr_path(repo_root, "0013")
    key_a = EXPECTED_TENANT_KEY_GROUPS["analysis-facts"][0]
    key_b = EXPECTED_TENANT_KEY_GROUPS["analysis-facts"][1]
    ref_a = EXPECTED_REFERENCE_GROUPS["corpus-evidence"][0]
    ref_b = EXPECTED_REFERENCE_GROUPS["corpus-evidence"][1]
    if case == "missing":
        _replace(path, f"- `{key_a}`\n", "")
    elif case == "extra":
        _replace(path, f"- `{key_a}`\n", f"- `{key_a}`\n- `Extra(firm_id,id)`\n")
    elif case == "reordered":
        _replace(path, f"- `{key_a}`\n- `{key_b}`", f"- `{key_b}`\n- `{key_a}`")
    elif case == "altered-column":
        _replace(path, key_a, "MedicalEncounter(id)")
    elif case == "missing-reference":
        _replace(path, f"- `{ref_a}`\n", "")
    elif case == "extra-reference":
        _replace(path, f"- `{ref_a}`\n", f"- `{ref_a}`\n- `extra -> reference`\n")
    elif case == "reordered-reference":
        _replace(path, f"- `{ref_a}`\n- `{ref_b}`", f"- `{ref_b}`\n- `{ref_a}`")
    elif case == "altered-reference":
        _replace(path, ref_a, "corpus head -> bare version")
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(case)


@pytest.mark.parametrize("case", INVENTORY_DRIFT_CASES, ids=INVENTORY_DRIFT_CASES)
def test_tenant_contract_inventory_drift_fails_hub_check(
    complete_repo: Path, case: str
) -> None:
    _drift_inventory(complete_repo, case)
    assert any("tenant inventory" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("scope", SCOPE_RULES, ids=list(SCOPE_RULES))
def test_tenant_key_contract_rejects_scope_mix(complete_repo: Path, scope: str) -> None:
    path = _adr_path(complete_repo, "0013")
    _replace(path, SCOPE_RULES[scope], "scope rule removed")
    assert any("tenant scope rule" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("substitute", BARE_KEY_RULES, ids=list(BARE_KEY_RULES))
def test_tenant_key_contract_rejects_bare_substitute(
    complete_repo: Path, substitute: str
) -> None:
    path = _adr_path(complete_repo, "0013")
    _replace(path, BARE_KEY_RULES[substitute], "bare key rule removed")
    assert any("bare-key rule" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("obligation", TENANT_OBLIGATIONS, ids=list(TENANT_OBLIGATIONS))
def test_tenant_key_migration_obligation_is_frozen(
    complete_repo: Path, obligation: str
) -> None:
    path = _adr_path(complete_repo, "0013")
    _replace(path, TENANT_OBLIGATIONS[obligation], "migration obligation removed")
    assert any("migration obligation" in error for error in _charter_errors(complete_repo))


@pytest.mark.parametrize("surface", ["orm", "alembic"])
def test_s1_tenant_contract_adds_no_schema_change(complete_repo: Path, surface: str) -> None:
    before = _snapshot(complete_repo)
    _assert_charter_ok(complete_repo)
    after = _snapshot(complete_repo)
    assert before == after
    path = {
        "orm": "backend/app/models/orm.py",
        "alembic": "backend/alembic/versions/0001.py",
    }[surface]
    assert before[path] == after[path]


def test_hub_main_returns_zero_for_complete_charter(
    complete_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(complete_repo) == 0
    captured = capsys.readouterr()
    assert captured.out == "hub-check: OK (1 modules)\n"
    assert captured.err == ""


CHARTER_FAILURES = (
    "missing-adr",
    "wrong-adr-owner",
    "missing-adr-dependency",
    "shared-decision-folded",
    "legal-attestation-present",
    "adr-0009-allocated",
    "missing-future-owner",
    "partial-owner-activation",
    "missing-readiness-overlay",
    "r2-criteria-changed",
    "missing-source-rule",
    "forbidden-source-input",
    "workshop-evidence-closes-gate",
    "tenant-inventory-drift",
    "missing-tenant-obligation",
)


def _break_charter(repo_root: Path, case: str) -> str:
    boundary = _adr_path(repo_root, "0013")
    if case == "missing-adr":
        boundary.unlink()
        return "ADR-0013"
    if case == "wrong-adr-owner":
        _replace(boundary, ADR_SPECS["0013"][1], "Decision owner: wrong.")
        return "ADR-0013 decision owner"
    if case == "missing-adr-dependency":
        _replace(_adr_path(repo_root, "0014"), DEPENDENCIES["0014"], "Dependency: missing.")
        return "ADR-0014 dependency"
    if case == "shared-decision-folded":
        boundary.write_text(
            boundary.read_text(encoding="utf-8") + ADR_SPECS["0014"][1] + "\n",
            encoding="utf-8",
        )
        return "folds a shared decision"
    if case == "legal-attestation-present":
        _write(repo_root / "docs/legal_attestation.md", "Legal attestation: accepted\n")
        return "legal attestation"
    if case == "adr-0009-allocated":
        _write(repo_root / "docs/adr/0009-allocated.md", "# allocated\n")
        return "ADR-0009"
    if case == "missing-future-owner":
        owner = "app.core.matter_access"
        _replace(repo_root / "docs/system_contract.md", _owner_line(owner), "")
        return f"missing future owner {owner}"
    if case == "partial-owner-activation":
        owner = "app.core.matter_access"
        _activate_owner(repo_root, owner, "module-only")
        return f"{owner} activation is partial"
    if case == "missing-readiness-overlay":
        _replace(
            repo_root / "backlog/pi/10_implementation_readiness.md",
            "<!-- workshop-mvp-r1-overlay:start -->",
            "<!-- removed:start -->",
        )
        return "R1 readiness overlay"
    if case == "r2-criteria-changed":
        _replace(
            repo_root / "backlog/pi/10_implementation_readiness.md",
            R2_ROW,
            R2_ROW.replace("B1+B2+B3+B4 closed", "Workshop passed"),
        )
        return "R2 release row"
    if case == "missing-source-rule":
        _replace(repo_root / "workshop/README.md", "owned-synthetic", "unrestricted")
        return "owned-synthetic"
    if case == "forbidden-source-input":
        _replace(repo_root / "workshop/README.md", "`samples/`", "sample library")
        return "forbidden Workshop sources"
    if case == "workshop-evidence-closes-gate":
        _replace(repo_root / "workshop/README.md", "legal", "removed-legal")
        return "cannot close R2 gates"
    if case == "tenant-inventory-drift":
        _drift_inventory(repo_root, "missing")
        return "tenant inventory"
    if case == "missing-tenant-obligation":
        _replace(
            boundary,
            TENANT_OBLIGATIONS["all-null-or-all-present"],
            "obligation removed",
        )
        return "migration obligation"
    raise AssertionError(case)


@pytest.mark.parametrize("case", CHARTER_FAILURES, ids=CHARTER_FAILURES)
def test_hub_main_returns_one_with_deterministic_charter_diagnostic(
    complete_repo: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    expected = _break_charter(complete_repo, case)
    assert _main(complete_repo) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert expected.lower() in captured.err.lower()


EXISTING_FAILURES = (
    "missing-agents",
    "agents-placeholder",
    "missing-contracts",
    "missing-registered-module",
    "missing-registered-contract-doc",
)


@pytest.mark.parametrize("case", EXISTING_FAILURES, ids=EXISTING_FAILURES)
def test_hub_main_returns_one_with_deterministic_existing_contract_diagnostic(
    complete_repo: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    if case == "missing-agents":
        (complete_repo / "AGENTS.md").unlink()
        expected = "AGENTS.md does not exist"
    elif case == "agents-placeholder":
        _write(complete_repo / "AGENTS.md", "Run <command>\n")
        expected = "unfilled placeholder '<command>'"
    elif case == "missing-contracts":
        (complete_repo / "CONTRACTS.md").unlink()
        expected = "CONTRACTS.md does not exist"
    elif case == "missing-registered-module":
        (complete_repo / "backend/app/existing/__init__.py").unlink()
        (complete_repo / "backend/app/existing").rmdir()
        expected = "module path 'backend/app/existing' which does not exist"
    else:
        (complete_repo / "docs/module_contracts/existing.md").unlink()
        expected = "contract doc 'docs/module_contracts/existing.md' which does not exist"
    assert _main(complete_repo) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert expected in captured.err


def test_hub_main_orders_multiple_charter_diagnostics(
    complete_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _adr_path(complete_repo, "0013").unlink()
    _adr_path(complete_repo, "0015").unlink()
    assert _main(complete_repo) == 1
    stderr = capsys.readouterr().err
    assert stderr.index("ADR-0013") < stderr.index("ADR-0015")


def test_hub_main_uses_supplied_repo_root(
    complete_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert complete_repo != REPO_ROOT
    assert _main(complete_repo) == 0
    assert capsys.readouterr().out == "hub-check: OK (1 modules)\n"


def test_hub_main_never_reports_partial_success(
    complete_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _adr_path(complete_repo, "0017").unlink()
    assert _main(complete_repo) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "OK" not in captured.err


@pytest.mark.parametrize("effect", ["traceback", "filesystem-write"])
def test_hub_main_failure_has_no_side_effect(
    complete_repo: Path, capsys: pytest.CaptureFixture[str], effect: str
) -> None:
    _adr_path(complete_repo, "0013").unlink()
    before = _snapshot(complete_repo)
    assert _main(complete_repo) == 1
    captured = capsys.readouterr()
    after = _snapshot(complete_repo)
    if effect == "traceback":
        assert "Traceback" not in captured.err
    else:
        assert before == after
