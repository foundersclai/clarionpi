"""Typed errors for the rules layer.

Diagnostics the frontend can trust (the lawyer-audit boundary pattern): every rules failure
carries a ``diagnostic_kind`` string the FE renders on, never re-deriving. v1 is Arizona only,
so an unknown jurisdiction is a typed refusal — not a guess, not a silent fallback.
"""

from __future__ import annotations


class RulesError(Exception):
    """Base class for rules-layer errors; all carry a typed ``diagnostic_kind``."""

    diagnostic_kind: str = "rules_error"


class UnsupportedJurisdiction(RulesError):
    """Raised when no rule pack exists for a requested jurisdiction (v1 = AZ only).

    The typed ``diagnostic_kind`` lets the API map this to a stable wire body and the FE render
    an ``unavailable``-class refusal (flow_01 §6: non-AZ creation is refused, typed).
    """

    diagnostic_kind = "jurisdiction_unsupported"

    def __init__(self, jurisdiction: str) -> None:
        self.jurisdiction = jurisdiction
        super().__init__(f"no rule pack for jurisdiction {jurisdiction!r} (v1 supports AZ only)")


class RulePackInvalid(RulesError):
    """Raised at load time when a pack is malformed or ships unverified/unsafe law.

    Bad law must not run (fail loud, refuse to start) — jurisdiction_rules §4.
    """

    diagnostic_kind = "rule_pack_invalid"


class LetterStructureMissing(RulesError):
    """Raised when a pack lacks the ``letter_structure`` block Brain-2 drafting requires.

    Drafting needs the demand-letter section skeleton (the ordered section list); a pack without
    it cannot drive Brain-2. Fail loud — there is deliberately NO code-side default section set
    (a made-up skeleton would be unaudited law masquerading as a default).
    """

    diagnostic_kind = "letter_structure_missing"

    def __init__(self, jurisdiction_or_pack: str) -> None:
        self.pack = jurisdiction_or_pack
        super().__init__(
            f"rule pack {jurisdiction_or_pack!r} has no letter_structure block "
            "(Brain-2 drafting requires the demand-letter section skeleton)"
        )
