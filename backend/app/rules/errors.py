"""Typed errors for the rules layer.

Diagnostics the frontend can trust (the lawyer-audit boundary pattern): every rules failure
carries a ``diagnostic_kind`` string the FE renders on, never re-deriving. v1 is Arizona only,
so an unknown jurisdiction is a typed refusal — not a guess, not a silent fallback.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


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


@dataclass(frozen=True)
class IntakeScopeReason:
    """One per-flag refusal reason riding a :class:`MatterOutOfScope` (WI-2).

    ``reason`` is attorney-readable scope-boundary copy ("outside v1 supported scope"),
    never a system error and never legal advice — it travels to the wire verbatim.
    """

    flag: str  # the intake-flag field name, e.g. "public_entity_involved"
    answer: str  # the IntakeFlagAnswer value that triggered the refusal ("yes" | "unknown")
    reason: str


class MatterOutOfScope(RulesError):
    """Raised at matter creation when an intake answer places the matter outside the v1 box.

    A v1 scope boundary, not an error state: the message carries flag NAMES only (no client
    facts, no legal text) — the per-flag attorney copy rides ``reasons`` to the wire.
    """

    diagnostic_kind = "matter_out_of_scope"

    def __init__(self, reasons: Sequence[IntakeScopeReason]) -> None:
        self.reasons = list(reasons)
        flags = ", ".join(r.flag for r in self.reasons)
        super().__init__(f"matter is outside v1 supported scope ({flags})")


class RulePackInvalid(RulesError):
    """Raised at load time when a pack is malformed or ships unverified/unsafe law.

    Bad law must not run (fail loud, refuse to start) — jurisdiction_rules §4.
    """

    diagnostic_kind = "rule_pack_invalid"


class RulePackUnaudited(RulesError):
    """Raised when a consumer requires an AUTHORITATIVE pack and the pack is not (BUS-02).

    Carries jurisdiction + pack version only — deliberately no legal-source text, audit
    notes, or file paths (nothing sensitive rides the refusal to the wire).
    """

    diagnostic_kind = "rule_pack_unaudited"

    def __init__(self, jurisdiction: str, *, version: str) -> None:
        self.jurisdiction = jurisdiction
        self.version = version
        super().__init__(
            f"rule pack {jurisdiction!r} v{version} requires counsel audit before it can back "
            "a production package"
        )


class RulePackUnpinned(RulesError):
    """Raised when the audited-package guard is enabled and the matter carries no pack pin.

    A legacy matter processed before pinning existed cannot be silently attested against
    today's YAML — the guard fails closed (BUS-02).
    """

    diagnostic_kind = "rule_pack_unpinned"

    def __init__(self, jurisdiction: str) -> None:
        self.jurisdiction = jurisdiction
        super().__init__(
            f"matter has no rule-pack pin for {jurisdiction!r}; the audited-package guard "
            "refuses unpinned matters"
        )


class RulePackChanged(RulesError):
    """Raised when the current pack's version/fingerprint no longer matches a matter's pin.

    The matter's deadline, ledger, and drafting work attested to the PINNED pack; a changed
    (or changed-and-reverted) YAML cannot be consumed against that pin (BUS-02).
    """

    diagnostic_kind = "rule_pack_changed"

    def __init__(
        self, jurisdiction: str, *, pinned_version: str | None, current_version: str
    ) -> None:
        self.jurisdiction = jurisdiction
        self.pinned_version = pinned_version
        self.current_version = current_version
        super().__init__(
            f"rule pack {jurisdiction!r} drifted from the matter's pin "
            f"(pinned {pinned_version!r}, current {current_version!r})"
        )


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
