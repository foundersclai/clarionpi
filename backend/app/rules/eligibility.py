"""Pilot-intake eligibility — the v1 scope box at matter creation (WI-2).

The rules layer owns the "supported scope" decision (the ``UnsupportedJurisdiction``
precedent); this module is its engineer-owned Python half — no YAML, no lawyer-audited law,
just the deliberately restricted pilot box: adult AZ private-party MVA, ordinary open
demand. Four tri-state intake questions gate creation; any answer other than ``no``
refuses, typed. ``unknown`` refuses CONSERVATIVELY — the attorney resolves the question
and creates the matter then; nothing is guessed on their behalf.

Creation-time check ONLY: a stored answer (including the ``unknown`` backfilled onto rows
that predate the preflight) never blocks an existing matter's gate progress.

Copy discipline: every reason frames a v1 scope boundary ("outside v1 supported scope —
handle in your existing workflow"), never a system error and never legal advice. The
wording is attorney-facing legal-adjacent text — counsel reviews it before pilot
(pilot plan WI-2 risk note).
"""

from __future__ import annotations

from app.models.enums import IntakeFlagAnswer
from app.rules.errors import IntakeScopeReason, MatterOutOfScope

# Copy pending counsel review before pilot — scope-boundary wording only.
_OUT_OF_SCOPE_REASONS: dict[str, str] = {  # answer == "yes"
    "public_entity_involved": (
        "A claim involving a public entity is outside v1 supported scope — "
        "handle this matter in your existing workflow."
    ),
    "plaintiff_is_minor": (
        "A claim on behalf of a minor is outside v1 supported scope — "
        "handle this matter in your existing workflow."
    ),
    "wrongful_death": (
        "A wrongful-death claim is outside v1 supported scope — "
        "handle this matter in your existing workflow."
    ),
    "coverage_dispute": (
        "A claim with a coverage dispute is outside v1 supported scope — "
        "handle this matter in your existing workflow."
    ),
}

# Copy pending counsel review before pilot — "resolve, then create", never a guess.
_UNRESOLVED_REASONS: dict[str, str] = {  # answer == "unknown"
    "public_entity_involved": (
        "Confirm whether a public entity is involved, then create the matter — "
        "v1 accepts a matter only once this is answered 'no'."
    ),
    "plaintiff_is_minor": (
        "Confirm whether the plaintiff is a minor, then create the matter — "
        "v1 accepts a matter only once this is answered 'no'."
    ),
    "wrongful_death": (
        "Confirm whether this is a wrongful-death claim, then create the matter — "
        "v1 accepts a matter only once this is answered 'no'."
    ),
    "coverage_dispute": (
        "Confirm whether there is a coverage dispute, then create the matter — "
        "v1 accepts a matter only once this is answered 'no'."
    ),
}

# The canonical flag order — request field order, refusal order, and display order agree.
INTAKE_FLAG_NAMES: tuple[str, ...] = tuple(_OUT_OF_SCOPE_REASONS)


def check_pilot_eligibility(
    *,
    public_entity_involved: IntakeFlagAnswer,
    plaintiff_is_minor: IntakeFlagAnswer,
    wrongful_death: IntakeFlagAnswer,
    coverage_dispute: IntakeFlagAnswer,
) -> None:
    """Refuse (typed) any matter whose intake answers fall outside the v1 pilot box.

    Raises :class:`MatterOutOfScope` carrying ONE reason per offending flag — a
    multi-flag matter reports every boundary at once, not just the first, so the
    attorney sees the whole picture in a single refusal.
    """
    answers: dict[str, IntakeFlagAnswer] = {
        "public_entity_involved": public_entity_involved,
        "plaintiff_is_minor": plaintiff_is_minor,
        "wrongful_death": wrongful_death,
        "coverage_dispute": coverage_dispute,
    }
    reasons = [
        IntakeScopeReason(
            flag=flag,
            answer=answer.value,
            reason=(
                _OUT_OF_SCOPE_REASONS[flag]
                if answer is IntakeFlagAnswer.YES
                else _UNRESOLVED_REASONS[flag]
            ),
        )
        for flag, answer in answers.items()
        if answer is not IntakeFlagAnswer.NO
    ]
    if reasons:
        raise MatterOutOfScope(reasons)
