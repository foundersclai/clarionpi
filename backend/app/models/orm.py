"""SQLAlchemy 2.0 ORM — the persistence layer for ClarionPI.

Design rules (04 §2 invariants + AGENTS boundaries):

* **Portable types only.** Columns use ``sa.Uuid``, ``sa.JSON``, ``sa.String/Text/
  Integer/Date/DateTime(timezone=True)/Boolean``. Enums are stored as ``String`` (the
  ``StrEnum`` value), *not* ``sa.Enum`` — this keeps the schema identical on SQLite (tests,
  offline dev) and Postgres (deploy), and enum-value validation lives at the Pydantic layer.
* **All money is integer cents.** Every currency column is ``Integer`` and named
  ``*_cents``. ``Float``/``Numeric`` appear nowhere except ``document_pages.ocr_confidence``
  (a confidence score, not currency).
* **Tenancy.** Every firm-scoped table carries ``firm_id`` (indexed, non-null) via the
  ``FirmScoped`` mixin — the ``firms`` table itself is the sole exception.
* **Derived state is not stored.** ``SPECIALS_LEDGER`` (money_engine) is a computed view, so
  there is no ledger table here (schema inv 2).
* ``created_at`` everywhere; ``updated_at`` only where rows legitimately mutate.
* ``audit_events`` is append-only by design — no ``updated_at`` column.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every ClarionPI table."""


class FirmScoped:
    """Mixin: every firm-scoped table carries an indexed, non-null ``firm_id``.

    This is the tenancy invariant from 04 §2 and the AGENTS boundary — ClarionPI is a
    captive multi-firm platform, so the firm id is the tenant filter on every row that is
    not the firm itself.
    """

    firm_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True, nullable=False)


def _pk() -> Mapped[uuid.UUID]:
    """Standard UUID primary key with a Python-side default."""
    return mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    """``created_at`` with a DB server default of now()."""
    return mapped_column(sa.DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    """``updated_at`` for tables whose rows legitimately mutate."""
    return mapped_column(
        sa.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------------------------
# Tenancy roots
# --------------------------------------------------------------------------------------


class Firm(Base):
    """A captive firm tenant. The only table without ``firm_id`` (it *is* the firm)."""

    __tablename__ = "firms"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class User(Base, FirmScoped):
    """A firm user; ``role`` drives server-side gate role guards (invariant 8)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    role: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # UserRole
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Matter + corpus
# --------------------------------------------------------------------------------------


class Matter(Base, FirmScoped):
    """The case. Root of everything downstream; carries the gate state + registry version."""

    __tablename__ = "matters"

    id: Mapped[uuid.UUID] = _pk()
    client_display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    claim_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # ClaimType
    incident_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(sa.String(64), nullable=False)  # state code
    venue_county: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    gate_state: Mapped[str] = mapped_column(sa.String(48), nullable=False)  # GateState
    registry_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    sol_candidates: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CaseDocument(Base, FirmScoped):
    """An uploaded document within a matter."""

    __tablename__ = "case_documents"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    doc_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # DocType
    source_label: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    filename: Mapped[str] = mapped_column(sa.String(512), nullable=False, default="")
    # Nullable: a failed/expired ingest path may never actually store a blob.
    storage_key: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    page_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    dedup_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # DedupStatus
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # DocStatus
    # Float is acceptable HERE ONLY: this is a classifier confidence score, not currency.
    classification_confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    failure_reason: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class DocumentPage(Base, FirmScoped):
    """One page of a case document; reaches the matter via its parent document FK.

    The ``(document_id, page_no)`` anchor is unique: a page's provenance identity never
    changes (inv 2). A re-OCR does not mutate this row — it appends a :class:`PageText` and
    moves ``active_text_id``.
    """

    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="uq_document_page_doc_page_no"),
    )

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("case_documents.id"), index=True, nullable=False
    )
    page_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    text_source: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # TextSource
    # Float is acceptable HERE ONLY: this is an OCR confidence score, not currency.
    ocr_confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    image_ref: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    zero_text: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    # Plain Uuid, deliberately NOT a ForeignKey: it points at page_texts.id, which itself FKs
    # back to this table. A DB-level circular FK can't be created via SQLite ALTER and buys
    # nothing here — the app moves this pointer when it appends a new PageText version (inv 2).
    active_text_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True, index=True)
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Upload sessions (M1 batch upload)
# --------------------------------------------------------------------------------------


class UploadSession(Base, FirmScoped):
    """A resumable batch-upload session; commit turns received slots into ``CaseDocument``s."""

    __tablename__ = "upload_sessions"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # UploadSessionStatus
    # Abandoned-session sweep deadline: sessions past this without a commit are expired.
    ttl_expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class UploadSlot(Base, FirmScoped):
    """One file slot in an upload session; carries the storage key + received flag."""

    __tablename__ = "upload_slots"

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("upload_sessions.id"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(sa.String(512), nullable=False)  # client name, display
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)  # bytes, not money
    storage_key: Mapped[str] = mapped_column(sa.String(1024), nullable=False)
    received: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("case_documents.id"), nullable=True
    )  # set at commit
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Page text history + dedup quarantine
# --------------------------------------------------------------------------------------


class PageText(Base, FirmScoped):
    """Append-only text-version history for a :class:`DocumentPage`.

    Page identity never mutates: a re-OCR appends a row here and moves
    ``DocumentPage.active_text_id`` (inv 2), so prior text versions stay auditable.
    """

    __tablename__ = "page_texts"

    id: Mapped[uuid.UUID] = _pk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("document_pages.id"), index=True, nullable=False
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    text_source: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # TextSource
    # Float is acceptable HERE ONLY: this is an OCR confidence score, not currency.
    ocr_confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    engine: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)  # OCR engine id
    created_at: Mapped[datetime] = _created_at()


class DedupDecision(Base, FirmScoped):
    """A quarantined dedup verdict. NEVER auto-merged: a human resolves kept vs superseded."""

    __tablename__ = "dedup_decisions"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    # The NEW doc under suspicion.
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("case_documents.id"), index=True, nullable=False
    )
    # The earlier doc it collides with (nullable: a decision may await pairing).
    against_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("case_documents.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # DedupStatus
    # [[this_page_no, other_page_no], ...] — the colliding page pairs.
    page_hash_matches: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    # Float is acceptable HERE ONLY: this is a shingle-similarity score, not currency.
    shingle_overlap: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    resolution: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # DedupResolution
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Extracted facts (Brain-1 upstream)
# --------------------------------------------------------------------------------------


class MedicalEncounter(Base, FirmScoped):
    """A tokenized medical encounter extracted from records. ``anchors`` is non-empty (inv 1)."""

    __tablename__ = "medical_encounters"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    date_of_service: Mapped[date] = mapped_column(sa.Date, nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    facility: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    encounter_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    complaints: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    findings: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    diagnoses: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    procedures: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    work_status: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    narrative_tokenized: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    anchors: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    merged_from: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()


class BillingLine(Base, FirmScoped):
    """A single billing line. Money columns are integer cents; ``anchor`` is required (inv 1)."""

    __tablename__ = "billing_lines"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    date_of_service: Mapped[date] = mapped_column(sa.Date, nullable=False)
    code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    billed_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    adjusted_cents: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    paid_cents: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    outstanding_cents: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    category: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # LedgerCategory
    anchor: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class IncidentFacts(Base, FirmScoped):
    """Matter-scoped one-row incident-facts payload (police report + intake)."""

    __tablename__ = "incident_facts"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False, unique=True
    )
    payload: Mapped[dict] = mapped_column(sa.JSON, nullable=False, default=dict)
    anchors: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Risk flags (Brain-1 / G2a)
# --------------------------------------------------------------------------------------


class RiskFlag(Base, FirmScoped):
    """An anchored adverse/case-risk flag requiring human disposition at G2a."""

    __tablename__ = "risk_flags"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(sa.String(48), nullable=False)  # FlagKind
    severity: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # FlagSeverity
    anchors: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    detail: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    disposition: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)  # FlagDisposition
    disposition_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
    disposition_rationale: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# --------------------------------------------------------------------------------------
# Fact registry (the spine)
# --------------------------------------------------------------------------------------


class FactToken(Base, FirmScoped):
    """A versioned, typed fact token. ``token_id`` (e.g. ``FACT_12``) is stable per slot.

    Uniqueness is (matter_id, token_id, registry_version): a superseded value is a new
    version row, never a recycled id (fact_registry §4).
    """

    __tablename__ = "fact_tokens"
    __table_args__ = (
        UniqueConstraint(
            "matter_id", "token_id", "registry_version", name="uq_fact_token_matter_id_version"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    token_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)  # e.g. "FACT_12"
    registry_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # TokenKind
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        sa.JSON, nullable=True
    )
    display_form: Mapped[str] = mapped_column(sa.Text, nullable=False)
    anchors: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # TokenStatus
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # TokenSource
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Strategy (G1.5 inputs, G2.5 plan)
# --------------------------------------------------------------------------------------


class StrategyInputs(Base, FirmScoped):
    """Verbatim attorney strategy inputs captured at G1.5."""

    __tablename__ = "strategy_inputs"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False, unique=True
    )
    liability_theory: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    injury_framing: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    emphasis_notes: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    anchor_amount_cents: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    venue_posture: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    created_at: Mapped[datetime] = _created_at()


class StrategyPlan(Base, FirmScoped):
    """The G2.5 drafting contract. Approval binds ``registry_version`` (schema inv 3)."""

    __tablename__ = "strategy_plans"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    registry_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    demand_amount_cents: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    demand_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # open|time_limited
    sections: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    emphasis_directives: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    approved: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# --------------------------------------------------------------------------------------
# Demand draft (Brain-2) + compliance (G3)
# --------------------------------------------------------------------------------------


class DemandDraft(Base, FirmScoped):
    """A versioned demand draft; binds to a ``StrategyPlan`` version + registry version."""

    __tablename__ = "demand_drafts"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    registry_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class DraftSection(Base, FirmScoped):
    """A section of a demand draft; reaches the matter via its parent draft FK."""

    __tablename__ = "draft_sections"

    id: Mapped[uuid.UUID] = _pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("demand_drafts.id"), index=True, nullable=False
    )
    section_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    content_tokenized: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    rendered_preview: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class ComplianceFinding(Base, FirmScoped):
    """A G3-panel finding; reaches the matter via its parent draft FK."""

    __tablename__ = "compliance_findings"

    id: Mapped[uuid.UUID] = _pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("demand_drafts.id"), index=True, nullable=False
    )
    check_kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    bucket: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # FindingBucket
    gating: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # FindingGating
    detail: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    dispositioned: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    override_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Audit + gates
# --------------------------------------------------------------------------------------


class GateRecord(Base, FirmScoped):
    """One row per gate transition (invariant 9). Idempotent on (matter, gate, key)."""

    __tablename__ = "gate_records"
    __table_args__ = (
        UniqueConstraint(
            "matter_id", "gate", "idempotency_key", name="uq_gate_record_matter_gate_key"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    gate: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    action: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # GateAction
    actor_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("users.id"), nullable=False)
    actor_role: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # UserRole
    payload_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    override_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class AuditEvent(Base, FirmScoped):
    """Append-only audit log. No ``updated_at`` — immutability enforced in the core wave."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _pk()
    event_kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(sa.JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Package + telemetry + budget
# --------------------------------------------------------------------------------------


class Exhibit(Base, FirmScoped):
    """An exhibit collating pages of a source document; reaches the matter via document FK."""

    __tablename__ = "exhibits"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("case_documents.id"), index=True, nullable=False
    )
    exhibit_no: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    include_pages: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()


class LlmCall(Base, FirmScoped):
    """A metered LLM call. ``cost_cents`` is integer cents (money) — never a float."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=True
    )
    stage: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created_at()


class MatterBudget(Base, FirmScoped):
    """Per-matter spend cap. Both columns are integer cents (money)."""

    __tablename__ = "matter_budgets"

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False, unique=True
    )
    cap_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    spent_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    warned: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
