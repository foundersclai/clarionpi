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

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from app.models.enums import DeadlineKind, RuleVerifyStatus
from app.rules.errors import RulePackInvalid, UnsupportedJurisdiction

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


class RulePack(BaseModel):
    """A jurisdiction's validated rule pack.

    ``audited`` is informational at M0 (the stub pack ships ``audited: false``); the invariant-4
    guard is that every produced :class:`~app.models.schemas.DeadlineCandidate` carries the
    row's ``verify_status`` and assumptions for attorney confirmation.
    """

    model_config = ConfigDict(extra="forbid")

    pack: str
    version: str
    audited: bool = False
    deadline_rules: list[RuleRow] = []


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
