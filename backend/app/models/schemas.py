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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    CheckKind,
    ClaimType,
    DeadlineKind,
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    FindingBucket,
    FindingDisposition,
    FindingGating,
    FindingStatus,
    FlagDetector,
    FlagDisposition,
    FlagKind,
    FlagSeverity,
    GateAction,
    GateState,
    LedgerCategory,
    RuleVerifyStatus,
    SectionValidation,
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
    """A (doc, page) provenance anchor.

    Two provenance roles share this one shape:

    * **Stored provenance** (M1): the anchor persisted on a :class:`MedicalEncounter`,
      :class:`BillingLine`, or :class:`FactToken`; ``bbox`` (x0, y0, x1, y1) is the optional
      on-page region.
    * **Extraction emission** (M2): the anchor a model emits during an
      :class:`~app.models.orm.ExtractionRun`; ``window_id`` records *which* window the model was
      shown when it produced this anchor (the anti-fabrication validation target — a page
      outside the window is a fabricated cite), and ``field`` names the extracted field the
      anchor supports.

    The doc id field is ``document_id`` (M1 stored shape) rather than the ``doc_id`` the M2 spec
    sketched, so the single anchor shape stays backward-compatible with already-persisted rows;
    the extraction fields (``window_id``, ``field``) are additive and optional.
    """

    document_id: uuid.UUID
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None = None
    window_id: str | None = None
    field: str | None = None


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


class SpanRef(BaseModel):
    """A ``{start, end}`` char-offset range into a section's rendered text.

    The mechanical-splice target a compliance finding carries (``ComplianceFinding.span``). ``end``
    is exclusive and strictly positive; ``start`` is a non-negative offset.
    """

    start: int = Field(ge=0)
    end: int = Field(gt=0)


class RenderedSpan(BaseModel):
    """One rendered char-offset span minted at render time (``DraftSection.spans`` element).

    Feeds M6 provenance click-through: ``[start, end)`` into the section's rendered text is the
    resolved text for ``token_id``. ``token_id`` is the **BARE** registry id (e.g. ``"FACT_3"``,
    ``"AMT_1"``, ``"EX_2"``) — never the bracketed ``[[FACT_3]]`` token-shaped string on this
    shape (inv 11: nothing token-shaped on the wire; this carries the bare id).
    """

    span_id: str
    start: int
    end: int
    token_id: str


# --------------------------------------------------------------------------------------
# Extraction I/O schemas (M2) — the cross-wave extractor contracts
# --------------------------------------------------------------------------------------
#
# These are the *validated shapes* of what the medical-records / billing / incident
# extractors emit for a single window. Normalization (dollar strings -> cents, date parsing
# beyond ISO, dedup/merge) lives downstream; these carry the model's JSON as-read. Anchor
# pages are 1-based absolute page numbers within the window's span, so an anchor can be
# checked against the ExtractionRun window that produced it (anti-fabrication).


class ExtractedEncounter(BaseModel):
    """Structured output of the medical-records extractor for ONE encounter.

    Pre-normalization: the raw JSON the model returns, validated into shape. ``anchor_pages``
    is non-empty (every extracted encounter must cite at least one page) and holds 1-based
    absolute page numbers within the window's span.
    """

    date_of_service: date
    provider: str = Field(min_length=1)
    facility: str = ""
    encounter_type: str = Field(min_length=1)
    complaints: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    diagnoses: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    work_status: str | None = None
    anchor_pages: list[int] = Field(min_length=1)
    field_confidence: dict[str, float] = Field(default_factory=dict)


class ExtractedEncounterBatch(BaseModel):
    """A window's worth of extracted encounters."""

    encounters: list[ExtractedEncounter] = Field(default_factory=list)


class ExtractedBillingLine(BaseModel):
    """One billing line as read from a bill (pre-normalization).

    Money fields are dollar *strings* exactly as read (e.g. ``"$1,234.56"``); the money engine's
    deterministic code normalizes them to integer cents. ``category`` is validated into the
    fixed ledger taxonomy. ``anchor_page`` is a single 1-based absolute page number.
    """

    provider: str = Field(min_length=1)
    date_of_service: date
    code: str | None = None
    billed: str = Field(min_length=1)
    adjusted: str | None = None
    paid: str | None = None
    outstanding: str | None = None
    category: LedgerCategory
    anchor_page: int = Field(ge=1)


class ExtractedBillingBatch(BaseModel):
    """A window's worth of extracted billing lines."""

    lines: list[ExtractedBillingLine] = Field(default_factory=list)


class ExtractedIncident(BaseModel):
    """Structured output of the incident (police-report) extractor.

    ``parties`` is a list of ``{name, role}`` maps. ``anchor_pages`` is non-empty (1-based,
    within the window span).
    """

    location: str = ""
    incident_narrative: str = ""
    parties: list[dict[str, str]] = Field(default_factory=list)
    citations_issued: list[str] = Field(default_factory=list)
    anchor_pages: list[int] = Field(min_length=1)


class AmountFact(BaseModel):
    """money_engine -> fact_registry AMT emission payload.

    The division of labor: money never mints tokens, the registry never sums money. The money
    engine computes a value and hands the registry this payload; ``key`` is the deterministic
    ledger key (e.g. ``"specials.grand.billed"``, ``"specials.category.imaging.billed"``,
    ``"specials.demand_basis"``), ``value_cents`` is integer cents, and ``ledger_ref`` +
    ``ledger_hash`` pin the linkage back to the exact ledger state that produced it.
    """

    key: str
    value_cents: Cents
    display_form: str
    # {"line_ids": [...], "category": str | None, "column": str}
    ledger_ref: dict = Field(default_factory=dict)
    ledger_hash: str


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
    """Risk-flag view — anchored adverse fact awaiting disposition.

    ``detector`` is the flag's provenance (deterministic date/amount math vs the LLM labeling
    pass); the G2a workbench renders it so an attorney can see *how* a flag was produced.
    ``disposition_role`` is the actor's role captured at disposition time (an audit
    denormalization — the GateRecord remains the authoritative trail).
    """

    id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    kind: FlagKind
    severity: FlagSeverity
    detector: FlagDetector
    anchors: list[PageAnchor] = Field(default_factory=list)
    detail: str
    disposition: FlagDisposition | None = None
    disposition_by: uuid.UUID | None = None
    disposition_role: UserRole | None = None
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
    # G2.5-approve audit denorm (the GateRecord stays authoritative).
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
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
# Gate submit / edit schemas (M3 Wave B) — all CLOSED (extra="forbid", the anti-echo
# discipline: a submit must never round-trip fields the server did not define, so an AI
# overlay echoed back by a buggy FE is rejected at the boundary, not silently absorbed).
# --------------------------------------------------------------------------------------


class DeadlineConfirmation(BaseModel):
    """One per-candidate G1 confirm act (pinned design D1: deadline confirm is PER-CANDIDATE).

    ``rule_id`` is the candidate's ``statute_cite`` — the only stable identifier a
    :class:`DeadlineCandidate` carries (rule-pack rows have no synthetic ids; the cite is the
    lawyer-audited identity of the rule). ``confirmed=False`` is legal: an attorney may
    un-confirm a candidate they confirmed in error. ``confirmed`` here is the attorney's G1
    act; the candidate's ``verify_status`` is the orthogonal lawyer-audit status of the rule
    text itself — confirming a deadline does not verify the underlying statute.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    confirmed: bool


class FactsReviewEdits(BaseModel):
    """G1 (facts_review) edit payload: per-candidate confirmations + intake-fact updates.

    ``incident_facts`` is a shallow ``str -> str`` payload merge (coverage details etc.) into
    the matter's :class:`~app.models.orm.IncidentFacts` row — attorney-supplied intake facts.
    """

    model_config = ConfigDict(extra="forbid")

    deadline_confirmations: list[DeadlineConfirmation] = Field(default_factory=list)
    incident_facts: dict[str, str] | None = None


class StrategyIntakeEdits(BaseModel):
    """G1.5 (strategy_intake) edit payload — upserted VERBATIM into StrategyInputs.

    Only non-``None`` fields are applied; attorney text is preserved exactly as typed (no
    trimming, no normalization — the strategy memo is the attorney's voice). ``mmi_date`` /
    ``property_damage_estimate_cents`` are the M4 pull-forward fields (design D2: MMI is
    attorney-set at G1.5, never inferred; the treatment-gap and low-property-damage
    detectors read these).
    """

    model_config = ConfigDict(extra="forbid")

    liability_theory: str | None = None
    injury_framing: str | None = None
    emphasis_notes: str | None = None
    venue_posture: str | None = None
    anchor_amount_cents: Cents | None = None
    mmi_date: date | None = None
    property_damage_estimate_cents: Cents | None = None


class PlannedSectionEdit(BaseModel):
    """One G2.5 per-section plan edit — a partial override of a :class:`PlannedSection` slot.

    ``section_id`` names the existing planned section (matched against the latest plan; an unknown
    id is a typed 422 in the service — a plan edit must not invent a section). Every other field is
    optional and applied only when non-``None``: a ``None`` leaves the emitted allocation as-is.
    Token lists carry **bare** ids (``"FACT_3"``, ``"AMT_1"``) — never the bracketed token-shaped
    string (inv 11; the wire never sees token shape). "Edits re-emit the plan, not prose" (C5): this
    refines the drafting contract the deterministic emit produced, it does not touch section bodies.
    """

    model_config = ConfigDict(extra="forbid")

    section_id: str
    max_words: int | None = None
    allowed_tokens: list[str] | None = None
    required_tokens: list[str] | None = None


class PlanReviewEdits(BaseModel):
    """G2.5 (plan_review) edit payload — a partial override that RE-EMITS a new plan version.

    Applying these creates a NEW :class:`~app.models.orm.StrategyPlan` version (a copy of the
    latest, with the non-``None`` fields applied and each :class:`PlannedSectionEdit` merged onto
    its
    section by ``section_id``), ``approved=False`` and re-pinned to the matter's current
    ``registry_version`` — the attorney refines the drafting contract, then approves that version at
    G2.5. ``demand_type`` is the closed set: only ``"open"`` at v1 (a ``time_limited`` demand is a
    later version — the seam is here, the value is not yet accepted). "Edits re-emit the plan, not
    prose" (C5): no section body is authored here; Brain-2 drafts off the approved plan.
    """

    model_config = ConfigDict(extra="forbid")

    demand_amount_cents: Cents | None = None
    demand_type: Literal["open"] | None = None  # time_limited seam (a later version)
    emphasis_directives: list[str] | None = None
    sections: list[PlannedSectionEdit] | None = None


class GateSubmit(BaseModel):
    """A gate action submission (POST /api/matters/{id}/gates/{gate}/submit).

    ``idempotency_key`` is client-minted and unique per matter (pinned design D3): a duplicate
    submit replays the first outcome, writing no new record. ``payload_version`` is the
    optimistic-concurrency check (``matter.registry_version + gate-record count``); a stale
    value is refused with the fresh version. ``edits`` is validated **per-gate in the
    service** — the union here cannot discriminate without the path's gate, but each typed
    member is closed so unknown keys inside are rejected either way.
    """

    model_config = ConfigDict(extra="forbid")

    action: GateAction
    idempotency_key: str = Field(min_length=8, max_length=64)
    payload_version: int
    override_reason: str | None = None
    edits: FactsReviewEdits | StrategyIntakeEdits | PlanReviewEdits | dict | None = None


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


# --------------------------------------------------------------------------------------
# G2a evidence-workbench + risk-flag input schemas (M4) — all CLOSED (extra="forbid"),
# same anti-echo discipline as the M3 gate submits.
# --------------------------------------------------------------------------------------


class FlagDispositionRequest(BaseModel):
    """Attorney disposition of a single risk flag at G2a.

    ``rationale`` is REQUIRED (non-blank) when the disposition is ``omit_with_rationale`` — an
    attorney who drops an adverse fact from the letter must record why (the audit trail for the
    omission). It is optional for ``address_in_letter`` / ``need_more_records``.
    """

    model_config = ConfigDict(extra="forbid")

    disposition: FlagDisposition
    rationale: str | None = None

    @model_validator(mode="after")
    def _omit_requires_rationale(self) -> FlagDispositionRequest:
        if self.disposition is FlagDisposition.OMIT_WITH_RATIONALE and not (
            self.rationale and self.rationale.strip()
        ):
            raise ValueError("omit_with_rationale requires a non-blank rationale")
        return self


class ExhibitPickRequest(BaseModel):
    """A per-document exhibit pick at G2a — the page-level include/exclude lists + order.

    Pages are 1-based. A page in neither list is "not yet decided" (only ``include_pages``
    collate). ``sort_order`` is this exhibit's slot in the manifest collation order.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    include_pages: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    excluded_pages: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    sort_order: int = 0


class BillingLineEdit(BaseModel):
    """One edit to a SOURCE billing-line row from the G2a ledger grid.

    The grid writes source rows only (never the computed SPECIALS_LEDGER view, which is
    derived). Money fields are dollar *strings* (e.g. ``"$1,234.56"``) parsed to integer cents
    by ``app.money.types`` at the service layer — the schema carries the string as-typed; only
    non-``None`` fields are applied.
    """

    model_config = ConfigDict(extra="forbid")

    billing_line_id: uuid.UUID
    category: LedgerCategory | None = None
    billed: str | None = None
    adjusted: str | None = None
    paid: str | None = None
    outstanding: str | None = None


class BillingLineEditBatch(BaseModel):
    """A batch of source-row billing-line edits from the ledger grid."""

    model_config = ConfigDict(extra="forbid")

    edits: list[BillingLineEdit] = Field(min_length=1)


# The closed vocabulary of chronology-overlay edit keys. The date of service is the chronology
# SPINE (it orders every row and feeds the treatment-gap detector), so it is deliberately NOT
# overridable here — a wrong DOS is fixed by re-extraction, not a display overlay. `provider` /
# `facility` / `encounter_type` are the display fields a paralegal corrects; `narrative_override`
# supersedes the generated tokens-only narrative with the paralegal's own text.
_OVERLAY_EDIT_KEYS: frozenset[str] = frozenset(
    {"narrative_override", "provider_display", "facility_display", "encounter_type"}
)


class ChronologyOverlayRequest(BaseModel):
    """A paralegal's chronology-row overlay edit at G2a — the CLOSED edited-fields set.

    ``edited_fields`` is validated against the closed :data:`_OVERLAY_EDIT_KEYS` vocabulary: an
    unknown key or a non-string value is a ``ValueError`` (the route maps the validation failure to
    a ``422 invalid_edits``). An **empty** dict is also rejected — clearing an overlay is out of
    scope at M4, so an empty edit set is a no-op the route must not accept (a `PUT` that means to
    clear has nothing to write). The whole set replaces any prior overlay wholesale (an overlay is
    the full edit set for a row, not a patch history).
    """

    model_config = ConfigDict(extra="forbid")

    edited_fields: dict[str, Any]

    @model_validator(mode="after")
    def _closed_vocabulary(self) -> ChronologyOverlayRequest:
        if not self.edited_fields:
            raise ValueError("edited_fields must be non-empty (clearing is out of scope at M4)")
        unknown = sorted(set(self.edited_fields) - _OVERLAY_EDIT_KEYS)
        if unknown:
            raise ValueError(
                f"unknown overlay edit key(s): {unknown}; allowed: {sorted(_OVERLAY_EDIT_KEYS)}"
            )
        non_str = sorted(k for k, v in self.edited_fields.items() if not isinstance(v, str))
        if non_str:
            raise ValueError(f"overlay edit value(s) must be strings: {non_str}")
        return self


class RiskLabelOutput(BaseModel):
    """LLM structured output for ONE flag from the risk-labeling pass.

    ``anchor_pages`` is non-empty (inv 2): an unanchored label fails the build — every risk flag
    must cite at least one page, 1-based within the labeled span.
    """

    kind: FlagKind
    severity: FlagSeverity
    detail: str
    anchor_pages: list[int] = Field(min_length=1)


class RiskLabelBatch(BaseModel):
    """A labeling pass's worth of risk-flag outputs."""

    flags: list[RiskLabelOutput] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Brain-2 (drafting) + compliance (G3) I/O schemas (M5) — the cross-wave contracts.
# LLM structured outputs are OPEN (a model returns exactly the declared field); the attorney
# action request is CLOSED (extra="forbid", the anti-echo discipline).
# --------------------------------------------------------------------------------------


class SectionDraftOutput(BaseModel):
    """The per-section drafter's structured output (Opus).

    ``body_tokenized`` is tokens-only section prose (brain2 inv 5 — zero raw provider names,
    amounts, or citations; dollar figures are ``[[AMT_n]]`` references). Non-empty: an empty
    section is a validator reject.
    """

    body_tokenized: str = Field(min_length=1)


class MemoOutput(BaseModel):
    """The strategy-memo generator's structured output (Opus)."""

    memo: str = Field(min_length=1)


class PlanEmphasisOutput(BaseModel):
    """The Opus emphasis-synthesis output — the emphasis directives distilled for the plan."""

    emphasis_directives: list[str] = Field(default_factory=list)


# The three semantic check kinds the Sonnet judge may return. Deterministic verdicts are the
# code predicates' to make (brain2/compliance inv 13) — the judge may not claim a mechanical
# finding, so a JudgeFinding is validated against exactly this set.
_SEMANTIC_CHECK_KINDS: frozenset[CheckKind] = frozenset(
    {CheckKind.UNSUPPORTED_CAUSATION, CheckKind.STRATEGY_DRIFT, CheckKind.TONE}
)


class JudgeFinding(BaseModel):
    """One semantic finding from the Sonnet judge.

    ``check_kind`` is validated to be one of the THREE semantic kinds
    (``unsupported_causation`` / ``strategy_drift`` / ``tone``); a mechanical kind is rejected —
    the judge grades semantics, deterministic code owns mechanical verdicts (inv 13).
    """

    check_kind: CheckKind
    section_id: str
    detail: str = Field(min_length=1)
    severity: FindingGating = FindingGating.BLOCKING

    @model_validator(mode="after")
    def _only_semantic_kinds(self) -> JudgeFinding:
        if self.check_kind not in _SEMANTIC_CHECK_KINDS:
            allowed = sorted(k.value for k in _SEMANTIC_CHECK_KINDS)
            raise ValueError(
                f"judge may only return a semantic check_kind {allowed}; "
                f"got mechanical kind {self.check_kind.value!r}"
            )
        return self


class JudgeFindingBatch(BaseModel):
    """A judge pass's worth of semantic findings."""

    findings: list[JudgeFinding] = Field(default_factory=list)


class FindingActionRequest(BaseModel):
    """An attorney action on a single G3 compliance finding (CLOSED, anti-echo).

    ``action`` is the closed set ``{patch, regen, accept, override}``. ``override`` REQUIRES a
    non-blank ``override_reason`` — proceeding past a finding must record why (the audit trail).
    ``patch`` = a mechanical span-patch, ``regen`` = a single-section regen, ``accept`` = take the
    fix as-is.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["patch", "regen", "accept", "override"]
    override_reason: str | None = None

    @model_validator(mode="after")
    def _override_requires_reason(self) -> FindingActionRequest:
        has_reason = bool(self.override_reason and self.override_reason.strip())
        if self.action == "override" and not has_reason:
            raise ValueError("override requires a non-blank override_reason")
        return self


# --------------------------------------------------------------------------------------
# Reshaped-row views (M5) — pydantic mirrors of the reshaped ORM rows.
# --------------------------------------------------------------------------------------


class DraftSectionView(_ORMModel):
    """Draft-section view — the reshaped ``DraftSection`` row (tokens-only body + render spans)."""

    id: uuid.UUID
    firm_id: uuid.UUID
    draft_id: uuid.UUID
    section_id: str
    purpose: str
    body_tokenized: str
    rendered_preview: str | None = None
    registry_version: int
    validation: SectionValidation
    spans: list[RenderedSpan] = Field(default_factory=list)
    sort_order: int
    created_at: datetime | None = None


class ComplianceFindingView(_ORMModel):
    """Compliance-finding view — the reshaped ``ComplianceFinding`` row (severity/status/span).

    ``severity`` uses the :class:`FindingGating` vocabulary (the ORM column is named ``severity``
    per the compliance contract; the enum class name is ``FindingGating``).
    """

    id: uuid.UUID
    firm_id: uuid.UUID
    draft_id: uuid.UUID
    section_id: str
    registry_version: int
    check_kind: CheckKind
    bucket: FindingBucket
    severity: FindingGating
    detail: str
    anchors: list[PageAnchor] = Field(default_factory=list)
    span: SpanRef | None = None
    status: FindingStatus
    disposition: FindingDisposition | None = None
    disposition_by: uuid.UUID | None = None
    override_reason: str | None = None
    created_at: datetime | None = None
