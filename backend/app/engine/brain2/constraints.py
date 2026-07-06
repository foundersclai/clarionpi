"""Late-bound hard constraints — the TM ``<final_hard_constraints>`` port, structured.

The drafter's most binding instruction layer is assembled LAST and appended to the very tail of
the user prompt, so nothing in the matter-directives layer can soften it (the inv-14 layering: a
late-bound block binds after the rest of the prompt). This module builds that block from the
matter's risk-flag dispositions (:class:`~app.models.orm.RiskFlag`) and, when active, the
jurisdiction's statutory required terms.

Three entry categories, in a FIXED deterministic order (address, then no-volunteer, then
statutory) — a stable prompt tail is a stable :class:`~app.engine.brain2.drafter` snapshot hash:

* **Address list (inv 6).** Flags dispositioned ``address_in_letter`` MUST be spoken to in the
  letter. Their entry is the flag's attorney-visible ``detail`` text, formatted
  ``"Address in the letter: <detail>"``. (A flag is not a token — its ``detail`` is attorney-facing
  prose, so it is quoted here directly, never resolved through the registry.)

* **No-volunteer set (inv 6).** Flags the attorney chose to ``omit_with_rationale`` or defer as
  ``need_more_records``, PLUS any adverse flag left UNDISPOSITIONED, become
  ``"Never mention or allude to: <detail>"`` — the no-volunteering rule, so the drafter is told
  what it must not surface. An undispositioned adverse fact is treated as no-volunteer here (the
  conservative default; the hard block for an undispositioned adverse is compliance's, at G3).

* **Statutory terms.** ``statutory_terms`` is empty at v1 (a time-limited demand — which carries
  statutory response-window language — is a later version). The seam exists so the constraint
  assembly does not change shape when it lands.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FlagDisposition
from app.models.orm import Matter, RiskFlag

# The header the late-bound block is appended under (kept verbatim — it is part of the snapshot).
_FINAL_HEADER = "FINAL HARD CONSTRAINTS (binding):"

# Entry format strings — deterministic, so the same disposition set always yields the same tail.
_ADDRESS_FMT = "Address in the letter: {detail}"
_NO_VOLUNTEER_FMT = "Never mention or allude to: {detail}"


@dataclass(frozen=True)
class HardConstraintInputs:
    """The structured late-bound hard constraints for one matter, pre-render.

    Three ordered tuples of attorney-visible detail strings (NOT tokens — a flag's ``detail`` is
    prose the attorney sees). :meth:`to_entries` flattens them into the fixed-order entry list the
    drafter appends.

    * ``address_display_forms`` — the ``detail`` text of every ``address_in_letter`` flag (these
      MUST be addressed).
    * ``no_volunteer_details`` — the ``detail`` text of every ``omit_with_rationale`` /
      ``need_more_records`` / UNDISPOSITIONED adverse flag (these must NOT surface).
    * ``statutory_terms`` — required statutory language (empty at v1; the seam for a time-limited
      demand).
    """

    address_display_forms: tuple[str, ...]
    no_volunteer_details: tuple[str, ...]
    statutory_terms: tuple[str, ...]

    def to_entries(self) -> list[str]:
        """Flatten to the FIXED-order entry list: address, then no-volunteer, then statutory.

        Deterministic and dedup-preserving-order: a detail that appears twice (e.g. two flags with
        identical text) collapses to its first occurrence, and the address/no-volunteer/statutory
        ordering never varies — the drafter's prompt tail (and thus its snapshot hash) is stable
        for a given disposition set.
        """
        seen: set[str] = set()
        entries: list[str] = []
        for detail in self.address_display_forms:
            entry = _ADDRESS_FMT.format(detail=detail)
            if entry not in seen:
                seen.add(entry)
                entries.append(entry)
        for detail in self.no_volunteer_details:
            entry = _NO_VOLUNTEER_FMT.format(detail=detail)
            if entry not in seen:
                seen.add(entry)
                entries.append(entry)
        for term in self.statutory_terms:
            if term not in seen:
                seen.add(term)
                entries.append(term)
        return entries


# The dispositions whose flags go into the no-volunteer set (must not surface).
_NO_VOLUNTEER_DISPOSITIONS: frozenset[str] = frozenset(
    {FlagDisposition.OMIT_WITH_RATIONALE.value, FlagDisposition.NEED_MORE_RECORDS.value}
)


def build_hard_constraints(db: Session, *, matter: Matter) -> HardConstraintInputs:
    """Build the matter's late-bound hard constraints from its risk-flag dispositions.

    Flags are read in a deterministic order (``created_at, id``) so the entry order is stable.
    Each flag is bucketed by its disposition:

    * ``address_in_letter`` -> ``address_display_forms`` (must be addressed);
    * ``omit_with_rationale`` / ``need_more_records`` -> ``no_volunteer_details`` (must not show);
    * disposition ``None`` (undispositioned) -> ``no_volunteer_details`` too — the conservative
      default (an undispositioned adverse fact is never volunteered; its hard G3 block is
      compliance's).

    ``statutory_terms`` is empty at v1 (the time-limited-demand seam).
    """
    flags = list(
        db.execute(
            select(RiskFlag)
            .where(RiskFlag.matter_id == matter.id)
            .order_by(RiskFlag.created_at, RiskFlag.id)
        ).scalars()
    )
    address: list[str] = []
    no_volunteer: list[str] = []
    for flag in flags:
        detail = flag.detail.strip()
        if not detail:
            continue
        if flag.disposition == FlagDisposition.ADDRESS_IN_LETTER.value:
            address.append(detail)
        elif flag.disposition in _NO_VOLUNTEER_DISPOSITIONS or flag.disposition is None:
            no_volunteer.append(detail)
    return HardConstraintInputs(
        address_display_forms=tuple(address),
        no_volunteer_details=tuple(no_volunteer),
        statutory_terms=(),
    )


def render_final_hard_constraints(entries: list[str]) -> str:
    """Render the late-bound hard-constraint block appended LAST to the drafter's user prompt.

    Format (a leading separator so it visually binds after everything before it)::

        \\n\\n---\\nFINAL HARD CONSTRAINTS (binding):\\n- <entry>\\n- <entry>

    An empty ``entries`` list still emits the header with no bullets — the drafter always sees the
    (possibly empty) binding block, so its presence is not a signal about the disposition set.
    """
    lines = "\n".join(f"- {entry}" for entry in entries)
    body = f"\n{lines}" if lines else ""
    return f"\n\n---\n{_FINAL_HEADER}{body}"
