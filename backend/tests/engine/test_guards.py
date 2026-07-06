"""Guard unit tests: pass/fail per guard, override path, and all-failures evaluate()."""

from __future__ import annotations

from dataclasses import replace

from app.engine.orchestrator import guards
from app.engine.orchestrator.guards import GuardContext
from app.engine.orchestrator.machine import Transition
from app.models.enums import GateState, UserRole

# A context where EVERY guard passes; individual tests mutate one field via replace().
BASE_OK = GuardContext(
    actor_role=UserRole.ATTORNEY,
    deadlines_confirmed=True,
    budget_available=True,
    registry_version_pinned=7,
    registry_version_current=7,
    open_high_severity_flags=0,
    override_reason=None,
    blocking_findings=0,
)


def _run(name: str, ctx: GuardContext) -> guards.GuardResult:
    return guards.REGISTRY[name](ctx)


# --- role_attorney ---------------------------------------------------------------------


def test_role_attorney_passes_for_attorney() -> None:
    assert _run("role_attorney", BASE_OK).passed is True


def test_role_attorney_fails_for_paralegal() -> None:
    result = _run("role_attorney", replace(BASE_OK, actor_role=UserRole.PARALEGAL))
    assert result.passed is False
    assert result.code == "role_not_attorney"


def test_role_attorney_admin_does_not_bypass() -> None:
    # Sign-off is personal: an admin is refused exactly like a paralegal (invariant 8).
    result = _run("role_attorney", replace(BASE_OK, actor_role=UserRole.ADMIN))
    assert result.passed is False
    assert result.code == "role_not_attorney"


def test_role_attorney_fails_for_none() -> None:
    result = _run("role_attorney", replace(BASE_OK, actor_role=None))
    assert result.passed is False


# --- deadlines_confirmed / budget_available (bool passthrough) -------------------------


def test_deadlines_confirmed_pass_and_fail() -> None:
    assert _run("deadlines_confirmed", BASE_OK).passed is True
    assert _run("deadlines_confirmed", replace(BASE_OK, deadlines_confirmed=False)).passed is False


def test_budget_available_pass_and_fail() -> None:
    assert _run("budget_available", BASE_OK).passed is True
    assert _run("budget_available", replace(BASE_OK, budget_available=False)).passed is False


# --- registry_version_match ------------------------------------------------------------


def test_registry_version_match_passes_when_equal() -> None:
    assert _run("registry_version_match", BASE_OK).passed is True


def test_registry_version_match_fails_when_unpinned() -> None:
    result = _run("registry_version_match", replace(BASE_OK, registry_version_pinned=None))
    assert result.passed is False
    assert result.code == "version_unpinned"


def test_registry_version_match_failure_detail_names_both_versions() -> None:
    result = _run(
        "registry_version_match",
        replace(BASE_OK, registry_version_pinned=7, registry_version_current=9),
    )
    assert result.passed is False
    assert result.code == "version_mismatch"
    # Detail must surface the exact delta so the UI can render "records changed".
    assert "7" in result.detail
    assert "9" in result.detail


# --- high_severity_dispositioned_or_override -------------------------------------------


def test_high_severity_clean_when_no_open_flags() -> None:
    result = _run("high_severity_dispositioned_or_override", BASE_OK)
    assert result.passed is True
    assert result.code == "clean"


def test_high_severity_override_path_returns_code_override() -> None:
    ctx = replace(BASE_OK, open_high_severity_flags=2, override_reason="attorney accepts risk")
    result = _run("high_severity_dispositioned_or_override", ctx)
    assert result.passed is True
    assert result.code == "override"


def test_high_severity_blocks_when_open_and_no_reason() -> None:
    ctx = replace(BASE_OK, open_high_severity_flags=1, override_reason=None)
    result = _run("high_severity_dispositioned_or_override", ctx)
    assert result.passed is False
    assert result.code == "high_severity_open"


def test_high_severity_blank_reason_does_not_count_as_override() -> None:
    ctx = replace(BASE_OK, open_high_severity_flags=1, override_reason="   ")
    result = _run("high_severity_dispositioned_or_override", ctx)
    assert result.passed is False


# --- no_blocking_findings --------------------------------------------------------------


def test_no_blocking_findings_pass_and_fail() -> None:
    assert _run("no_blocking_findings", BASE_OK).passed is True
    result = _run("no_blocking_findings", replace(BASE_OK, blocking_findings=3))
    assert result.passed is False
    assert result.code == "blocking_findings_open"


# --- evaluate() ------------------------------------------------------------------------


def test_evaluate_returns_empty_when_all_guards_pass() -> None:
    # G2.5 approve edge: role_attorney + registry_version_match + budget_available.
    transition = Transition(
        GateState.DRAFTING,
        ("role_attorney", "registry_version_match", "budget_available"),
    )
    assert guards.evaluate(transition, BASE_OK) == []


def test_evaluate_returns_all_failures_not_first_fail() -> None:
    # Two unmet guards: not an attorney AND no budget. Both must come back.
    transition = Transition(
        GateState.DRAFTING,
        ("role_attorney", "registry_version_match", "budget_available"),
    )
    ctx = replace(BASE_OK, actor_role=UserRole.PARALEGAL, budget_available=False)
    failures = guards.evaluate(transition, ctx)
    codes = {f.code for f in failures}
    assert codes == {"role_not_attorney", "budget_exhausted"}
    # registry_version_match still passed, so it is absent from the failures.
    assert "version_mismatch" not in codes


def test_evaluate_unconditional_transition_has_no_failures() -> None:
    transition = Transition(GateState.FACTS_REVIEW, ())
    assert guards.evaluate(transition, replace(BASE_OK, actor_role=None)) == []
