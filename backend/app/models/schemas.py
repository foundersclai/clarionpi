"""Pydantic v2 schemas — the shared type layer.

These mirror the ORM (``from_attributes=True`` so they load directly off ORM rows) but use
the real domain enums, so a string coming off the wire or the DB is *validated* into an enum
here — this is the enum-validation boundary the ORM defers to (see ``orm.py``).

Money discipline: cents are ``int``; every ``*_cents`` field uses the ``Cents`` alias
(non-negative int). Floats are banned for currency — the one float in the model
(``DocumentPage.ocr_confidence``) is a confidence score, not money.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ClaimType,
    DeadlineKind,
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    FlagDisposition,
    FlagKind,
    FlagSeverity,
    GateAction,
    GateState,
    LedgerCategory,
    RuleVerifyStatus,
    TextSource,
    TokenKind,
    TokenSource,
    TokenStatus,
    UserRole,
)

# All currency in the model is a non-negative integer number of cents.
Cents = Annotated[int, Field(ge=0)]


class _ORMModel(BaseModel):
    """Base for schemas that load off ORM rows."""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------------------


class PageAnchor(BaseModel):
    """A page-level provenance anchor. ``bbox`` is optional (x0, y0, x1, y1)."""

    document_id: uuid.UUID
    page: int
    bbox: tuple[float, float, float, float] | None = None


class DeadlineCandidate(BaseModel):
    """A rules-computed deadline, attorney-confirmed at G1 (schema inv 4)."""

    kind: DeadlineKind
    date: date
    statute_cite: str
    assumptions: list[str] = Field(default_factory=list)
    verify_status: RuleVerifyStatus
    confirmed: bool = False


class PlannedSection(BaseModel):
    """One planned demand section — the token budget for a section of the letter."""

    section_id: str
    purpose: str
    allowed_tokens: list[str] = Field(default_factory=list)
    required_tokens: list[str] = Field(default_factory=list)
    max_words: int


# --------------------------------------------------------------------------------------
# Entity schemas (mirror ORM, real enums)
# --------------------------------------------------------------------------------------


class Matter(_ORMModel):
    """Matter view — the case root."""

    id: uuid.UUID
    firm_id: uuid.UUID
    client_display_name: str
    claim_type: ClaimType
    incident_date: date
    jurisdiction: str
    venue_county: str | None = None
    gate_state: GateState
    registry_version: int
    sol_candidates: list[DeadlineCandidate] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CaseDocument(_ORMModel):
    """Case document view."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    doc_type: DocType
    source_label: str
    page_count: int
    dedup_status: DedupStatus
    status: DocStatus
    created_at: datetime | None = None


class DocumentPage(_ORMModel):
    """Document page view. ``ocr_confidence`` is a score, not currency."""

    id: uuid.UUID
    firm_id: uuid.UUID
    document_id: uuid.UUID
    page_no: int
    text: str
    text_source: TextSource
    ocr_confidence: float | None = None
    image_ref: str | None = None
    created_at: datetime | None = None


class MedicalEncounter(_ORMModel):
    """Medical encounter view. ``anchors`` must be non-empty in valid data (inv 1)."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    date_of_service: date
    provider: str
    facility: str
    encounter_type: str
    complaints: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    diagnoses: list[Any] = Field(default_factory=list)
    procedures: list[Any] = Field(default_factory=list)
    work_status: str | None = None
    narrative_tokenized: str
    anchors: list[PageAnchor] = Field(default_factory=list)
    merged_from: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime | None = None


class BillingLine(_ORMModel):
    """Billing line view. Money fields are cents; ``anchor`` is required (inv 1)."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    provider: str
    date_of_service: date
    code: str | None = None
    billed_cents: Cents
    adjusted_cents: Cents | None = None
    paid_cents: Cents | None = None
    outstanding_cents: Cents | None = None
    category: LedgerCategory
    anchor: PageAnchor
    created_at: datetime | None = None


class FactToken(_ORMModel):
    """Fact-token view — the versioned registry row (fact_registry)."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    token_id: str
    registry_version: int
    kind: TokenKind
    value: Any = None
    display_form: str
    anchors: list[PageAnchor] = Field(default_factory=list)
    status: TokenStatus
    source: TokenSource
    created_at: datetime | None = None


class RiskFlag(_ORMModel):
    """Risk-flag view — anchored adverse fact awaiting disposition."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    kind: FlagKind
    severity: FlagSeverity
    anchors: list[PageAnchor] = Field(default_factory=list)
    detail: str
    disposition: FlagDisposition | None = None
    disposition_by: uuid.UUID | None = None
    disposition_rationale: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StrategyInputs(_ORMModel):
    """Verbatim G1.5 attorney strategy inputs."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    liability_theory: str
    injury_framing: str
    emphasis_notes: str
    anchor_amount_cents: Cents | None = None
    venue_posture: str
    created_at: datetime | None = None


class StrategyPlan(_ORMModel):
    """G2.5 drafting contract. ``registry_version`` binds the approval (schema inv 3)."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    version: int
    registry_version: int
    demand_amount_cents: Cents | None = None
    demand_type: str
    sections: list[PlannedSection] = Field(default_factory=list)
    emphasis_directives: list[str] = Field(default_factory=list)
    approved: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GateRecord(_ORMModel):
    """Gate-transition audit row (invariant 9)."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    gate: str
    action: GateAction
    actor_id: uuid.UUID
    actor_role: UserRole
    payload_hash: str
    override_reason: str | None = None
    idempotency_key: str
    created_at: datetime | None = None


class LlmCall(_ORMModel):
    """Metered LLM-call view. ``cost_cents`` is integer cents."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID | None = None
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_cents: Cents
    created_at: datetime | None = None


class MatterBudget(_ORMModel):
    """Per-matter budget view. Both figures are integer cents."""

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    cap_cents: Cents
    spent_cents: Cents
    warned: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --------------------------------------------------------------------------------------
# Input schemas
# --------------------------------------------------------------------------------------


class MatterCreate(BaseModel):
    """Create-matter input. Enum validation rejects unsupported claim types."""

    client_display_name: str
    claim_type: ClaimType
    incident_date: date
    jurisdiction: str
    venue_county: str | None = None


# --------------------------------------------------------------------------------------
# Corpus-ingest input schemas (M1)
# --------------------------------------------------------------------------------------


class UploadFileDecl(BaseModel):
    """One client-declared file in an upload-session registration."""

    filename: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=0)


class UploadRegister(BaseModel):
    """Register a batch of files, opening an upload session with one slot per file."""

    files: list[UploadFileDecl] = Field(min_length=1)


class ReclassifyRequest(BaseModel):
    """Attorney override of a document's classification."""

    doc_type: DocType


class DedupResolveRequest(BaseModel):
    """Human resolution of a quarantined dedup decision. ``pending`` is not a valid action."""

    resolution: Literal[DedupResolution.KEPT, DedupResolution.SUPERSEDED]


class ClassifierOutput(BaseModel):
    """The classifier's structured output — parsed from the model's JSON reply."""

    doc_type: DocType
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""
