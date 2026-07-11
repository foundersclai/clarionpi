"""Pilot-intake eligibility unit tests (WI-2) — the pure v1 scope box, no HTTP.

The wire-level refusal shape is covered in ``tests/api/test_matters.py``; this suite pins
the rule itself: all-``no`` passes, every flag refuses on both ``yes`` and ``unknown`` with
DISTINCT copy, and a multi-flag matter reports every boundary in one refusal.
"""

from __future__ import annotations

import pytest

from app.models.enums import IntakeFlagAnswer
from app.rules.eligibility import INTAKE_FLAG_NAMES, check_pilot_eligibility
from app.rules.errors import MatterOutOfScope

_ALL_NO: dict[str, IntakeFlagAnswer] = {name: IntakeFlagAnswer.NO for name in INTAKE_FLAG_NAMES}


def test_all_no_passes_silently() -> None:
    check_pilot_eligibility(**_ALL_NO)  # must not raise


def test_flag_name_set_is_pinned() -> None:
    assert INTAKE_FLAG_NAMES == (
        "public_entity_involved",
        "plaintiff_is_minor",
        "wrongful_death",
        "coverage_dispute",
    )


@pytest.mark.parametrize("flag", INTAKE_FLAG_NAMES)
def test_yes_refuses_with_scope_boundary_copy(flag: str) -> None:
    with pytest.raises(MatterOutOfScope) as excinfo:
        check_pilot_eligibility(**(_ALL_NO | {flag: IntakeFlagAnswer.YES}))

    (reason,) = excinfo.value.reasons
    assert reason.flag == flag
    assert reason.answer == "yes"
    # Scope-boundary framing, never a system error and never legal advice.
    assert "outside v1 supported scope" in reason.reason
    assert "existing workflow" in reason.reason


@pytest.mark.parametrize("flag", INTAKE_FLAG_NAMES)
def test_unknown_refuses_conservatively_with_resolve_copy(flag: str) -> None:
    with pytest.raises(MatterOutOfScope) as excinfo:
        check_pilot_eligibility(**(_ALL_NO | {flag: IntakeFlagAnswer.UNKNOWN}))

    (reason,) = excinfo.value.reasons
    assert reason.flag == flag
    assert reason.answer == "unknown"
    # The copy says exactly what unblocks creation: resolve the question, answer 'no'.
    assert "then create the matter" in reason.reason
    assert "answered 'no'" in reason.reason


def test_multi_flag_refusal_reports_every_boundary_at_once() -> None:
    with pytest.raises(MatterOutOfScope) as excinfo:
        check_pilot_eligibility(
            public_entity_involved=IntakeFlagAnswer.YES,
            plaintiff_is_minor=IntakeFlagAnswer.NO,
            wrongful_death=IntakeFlagAnswer.UNKNOWN,
            coverage_dispute=IntakeFlagAnswer.YES,
        )

    reasons = excinfo.value.reasons
    assert [r.flag for r in reasons] == [
        "public_entity_involved",
        "wrongful_death",
        "coverage_dispute",
    ]
    assert [r.answer for r in reasons] == ["yes", "unknown", "yes"]


def test_exception_message_names_flags_only() -> None:
    """The exception string (the wire ``detail``) carries flag names, never client facts."""
    with pytest.raises(MatterOutOfScope) as excinfo:
        check_pilot_eligibility(**(_ALL_NO | {"wrongful_death": IntakeFlagAnswer.YES}))

    assert str(excinfo.value) == "matter is outside v1 supported scope (wrongful_death)"
