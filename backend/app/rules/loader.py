"""Rule-pack loader — YAML → a validated, typed :class:`RulePack`.

Consumers never see raw YAML (04 §5: only ``rules/`` reads jurisdiction YAML). The loader is
the boundary: it reads ``packs/<jurisdiction>.yaml``, validates it into pydantic models local to
this module, and hands downstream a typed structure. An unknown jurisdiction is a typed
:class:`~app.rules.errors.UnsupportedJurisdiction` (v1 = AZ only); a present-but-malformed pack
is :class:`~app.rules.errors.RulePackInvalid` (fail loud — bad law must not run).

``RulePack``/``RuleRow`` are *rules-local* pydantic models, deliberately separate from
``app.models.schemas`` — the pack file format is this module's private contract, not a wire
shape.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.models.enums import DeadlineKind, RuleVerifyStatus, TokenKind
from app.rules.errors import (
    LetterStructureMissing,
    RulePackChanged,
    RulePackInvalid,
    RulePackUnaudited,
    RulePackUnpinned,
    UnsupportedJurisdiction,
)

# The conservative-for-v1 basis when a pack omits the block: AZ v1 is billed-basis, and billed is
# the safe default (it never silently understates a demand by substituting paid where paid is
# absent). A typed default — surfaced through the accessor, logged nowhere.
_DEFAULT_BILLED_VS_PAID_BASIS = "billed"

_PACKS_DIR = Path(__file__).parent / "packs"


class RuleRow(BaseModel):
    """One deadline rule in a pack.

    Period is expressed as exactly one of ``years`` (SOL) or ``days`` (notice-of-claim); the
    validator enforces the right one per :attr:`kind`. ``applies_when`` is an informational
    predicate string surfaced to the attorney, not evaluated by code at v1.
    """

    model_config = ConfigDict(extra="forbid")

    kind: DeadlineKind
    claim_type: str | None = None
    applies_when: str | None = None
    years: int | None = None
    days: int | None = None
    statute_cite: str
    assumptions: list[str] = []
    verify_status: RuleVerifyStatus

    @model_validator(mode="after")
    def _check_period_matches_kind(self) -> RuleRow:
        if self.kind is DeadlineKind.SOL:
            if self.years is None or self.days is not None:
                raise ValueError("sol rule must set 'years' and not 'days'")
        elif self.kind is DeadlineKind.NOTICE_OF_CLAIM:
            if self.days is None or self.years is not None:
                raise ValueError("notice_of_claim rule must set 'days' and not 'years'")
        return self


class BilledVsPaidRule(BaseModel):
    """The jurisdiction's specials-ledger demand basis (money_engine consumes this).

    ``basis`` is ``"billed"`` or ``"paid"`` — whether the demand leads with the billed charges or
    the amounts actually paid (a substantive state-law question, e.g. AZ's collateral-source /
    *Lopez* line). Carries a ``source`` cite and a ``verify_status`` like every other pack row —
    unaudited law is surfaced, never hidden.
    """

    model_config = ConfigDict(extra="forbid")

    basis: Literal["billed", "paid"]
    source: str
    verify_status: RuleVerifyStatus


class LetterSectionRule(BaseModel):
    """One section of the demand-letter skeleton (Brain-2 drafting consumes these).

    ``max_words`` is the section's soft word ceiling; ``required_token_kinds`` are the fact-token
    kinds the section must carry (validated into :class:`~app.models.enums.TokenKind` — a YAML
    string like ``fact`` coerces to the enum, an unknown kind fails the pack load).
    """

    model_config = ConfigDict(extra="forbid")

    section_id: str
    purpose: str
    max_words: int
    required_token_kinds: list[TokenKind] = []


class LetterStructureRule(BaseModel):
    """The jurisdiction's demand-letter section skeleton (house drafting standard).

    The SECTION LIST is a legal-drafting judgment, so the block carries a ``source`` cite and a
    ``verify_status`` like every other pack row — an unaudited skeleton is surfaced, never hidden.
    ``sections`` is non-empty (a zero-section skeleton cannot drive drafting).
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    verify_status: RuleVerifyStatus
    sections: list[LetterSectionRule] = Field(min_length=1)


class RulePack(BaseModel):
    """A jurisdiction's validated rule pack.

    ``audited`` alone never makes a pack authoritative (BUS-02): flipping it to ``true``
    REQUIRES the counsel-audit provenance (``audited_by``, timezone-aware ``audited_at``,
    ``audit_reference``) — enforced by the model validator — and
    :attr:`is_authoritative` additionally requires every legal input drafting/package
    assembly consumes to be ``verified``. The invariant-4 guard remains: every produced
    :class:`~app.models.schemas.DeadlineCandidate` carries the row's ``verify_status`` and
    assumptions for attorney confirmation.
    """

    model_config = ConfigDict(extra="forbid")

    pack: str
    version: str
    audited: bool = False
    # Counsel-audit provenance (nullable while audited is false; REQUIRED once true).
    audited_by: str | None = None
    audited_at: datetime | None = None
    audit_reference: str | None = None
    audit_notes: str | None = None
    deadline_rules: list[RuleRow] = []
    # Optional: a pack without this block falls back to the documented conservative basis via the
    # accessor below (money_engine reads the basis, never the raw block).
    billed_vs_paid: BilledVsPaidRule | None = None
    # Optional in the parsed model (an arbitrary pack may not carry it), but drafting REQUIRES it:
    # the ``letter_sections`` accessor raises ``LetterStructureMissing`` when absent rather than
    # substituting a default skeleton (Brain-2 fails loud, never drafts against invented sections).
    letter_structure: LetterStructureRule | None = None

    @model_validator(mode="after")
    def _audited_requires_provenance(self) -> RulePack:
        if not self.audited:
            return self
        if not (self.audited_by and self.audited_by.strip()):
            raise ValueError("audited: true requires a non-empty audited_by")
        if self.audited_at is None or self.audited_at.tzinfo is None:
            raise ValueError("audited: true requires a timezone-aware audited_at")
        if not (self.audit_reference and self.audit_reference.strip()):
            raise ValueError("audited: true requires a non-empty audit_reference")
        return self

    @property
    def is_authoritative(self) -> bool:
        """Whether this pack may back a PRODUCTION demand package (BUS-02).

        Requires the counsel-audit flag + provenance AND that every legal input the
        pipeline consumes is ``verified``: a non-empty verified deadline-rule set, a
        present verified ``billed_vs_paid`` (the conservative fallback is fine for
        non-authoritative computation but cannot support a production package), and a
        present verified ``letter_structure`` (the drafted sections derive from it).
        """
        if not self.audited:
            return False
        # audited=True guarantees the provenance fields via the model validator.
        if not self.deadline_rules:
            return False
        if any(r.verify_status is not RuleVerifyStatus.VERIFIED for r in self.deadline_rules):
            return False
        if self.billed_vs_paid is None:
            return False
        if self.billed_vs_paid.verify_status is not RuleVerifyStatus.VERIFIED:
            return False
        if self.letter_structure is None:
            return False
        if self.letter_structure.verify_status is not RuleVerifyStatus.VERIFIED:
            return False
        return True

    @property
    def fingerprint(self) -> str:
        """Deterministic SHA-256 over the COMPLETE validated model (canonical JSON).

        The provenance pin for matters: it changes when audit metadata, any verification
        status, or any behavior-affecting legal input changes — a mutable YAML path or the
        version string alone is not sufficient provenance.
        """
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def letter_sections(self) -> list[LetterSectionRule]:
        """The demand-letter section skeleton for Brain-2 drafting.

        Raises :class:`~app.rules.errors.LetterStructureMissing` when the pack has no
        ``letter_structure`` block — drafting has no code-side default section set (an invented
        skeleton would be unaudited law). Returns the ordered section list when present.
        """
        if self.letter_structure is None:
            raise LetterStructureMissing(self.pack)
        return self.letter_structure.sections

    @property
    def billed_vs_paid_basis(self) -> str:
        """The specials-ledger demand basis for this jurisdiction.

        The pack row's ``basis`` when present, else :data:`_DEFAULT_BILLED_VS_PAID_BASIS` — the
        conservative-for-v1 default (AZ v1 is billed). This is the value :mod:`app.money.assemble`
        passes into ``build_specials_ledger``.
        """
        if self.billed_vs_paid is None:
            return _DEFAULT_BILLED_VS_PAID_BASIS
        return self.billed_vs_paid.basis


def _pack_path(jurisdiction: str) -> Path:
    return _PACKS_DIR / f"{jurisdiction.lower()}.yaml"


def load_pack(jurisdiction: str) -> RulePack:
    """Load and validate the rule pack for ``jurisdiction`` (case-insensitive).

    Raises :class:`~app.rules.errors.UnsupportedJurisdiction` when no pack file exists, and
    :class:`~app.rules.errors.RulePackInvalid` when the file is unparseable or fails schema
    validation.
    """
    path = _pack_path(jurisdiction)
    if not path.exists():
        raise UnsupportedJurisdiction(jurisdiction)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - defensive; stub pack parses cleanly
        raise RulePackInvalid(f"pack {path.name} is not valid YAML: {exc}") from exc
    try:
        return RulePack.model_validate(raw)
    except ValidationError as exc:
        raise RulePackInvalid(f"pack {path.name} failed schema validation: {exc}") from exc


def load_pack_for_pin(
    jurisdiction: str,
    version: str | None,
    fingerprint: str | None,
    *,
    require_authoritative: bool,
) -> RulePack:
    """Load a pack and verify it against a matter's pin (BUS-02) — the ONE door every
    post-create rules consumer that can feed the package goes through.

    - A pinned matter (``version`` + ``fingerprint`` set) requires the CURRENT pack to match
      both exactly; drift raises :class:`~app.rules.errors.RulePackChanged` — a pack edited
      after matter creation cannot be consumed mid-pipeline and quietly reverted before
      package build, and later audit flips cannot retroactively authorize earlier work.
    - An unpinned legacy matter passes when ``require_authoritative`` is false (dev/test
      behavior) and raises :class:`~app.rules.errors.RulePackUnpinned` when true — the
      package guard fails closed on missing pins.
    - ``require_authoritative=True`` additionally requires :attr:`RulePack.is_authoritative`
      (:class:`~app.rules.errors.RulePackUnaudited` otherwise). Earlier pipeline stages pass
      ``False``: they need pin CONSISTENCY but may exercise an unaudited pinned pack so
      local/demo workflows stay usable.
    """
    pack = load_pack(jurisdiction)
    if version is None and fingerprint is None:
        if require_authoritative:
            raise RulePackUnpinned(jurisdiction)
    elif pack.version != version or pack.fingerprint != fingerprint:
        raise RulePackChanged(jurisdiction, pinned_version=version, current_version=pack.version)
    if require_authoritative and not pack.is_authoritative:
        raise RulePackUnaudited(jurisdiction, version=pack.version)
    return pack
