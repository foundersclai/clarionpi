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
    # Argon2 hash of the user's password (M3 session auth). Nullable: a stub-mode seeded user
    # has no password until one is set, and the M0→M3 transition tolerates password-less rows.
    password_hash: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class AuthSession(Base, FirmScoped):
    """A server-side login session. The cookie carries an opaque token; only its sha256
    lands here — a DB leak exposes no usable credentials."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), index=True, nullable=False
    )
    # sha256 hexdigest (64 chars) of the raw token; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
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
    """One file slot in an upload session; carries the storage key + received flag.

    ``ordinal`` is the slot's zero-based position in the client's registration order — the
    stable pairing contract (BUS-06): the client matches browser files to slots by ordinal,
    never by response-array index. Unique per session by construction.
    """

    __tablename__ = "upload_slots"
    __table_args__ = (
        sa.UniqueConstraint("session_id", "ordinal", name="uq_upload_slot_session_ordinal"),
    )

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("upload_sessions.id"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(sa.Integer, nullable=False)  # registration order
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
    # Per-field extraction confidence (0..1) as JSON — a mapping, not a Float column, so the
    # money/Float-column ban is not tripped (scores live in JSON here, keyed by field name).
    field_confidence: Mapped[dict] = mapped_column(sa.JSON, nullable=False, default=dict)
    # Set on rows produced by a merge; carries which basis resolved the collision (# MergeBasis).
    merge_basis: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
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
    # How the numbers were sourced (# ReconciliationStatus); M2 emits only "llm_only".
    reconciliation: Mapped[str] = mapped_column(sa.String(24), nullable=False, default="llm_only")
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
# Extraction runs + chronology overlays (M2)
# --------------------------------------------------------------------------------------


class ExtractionRun(Base, FirmScoped):
    """One extraction window run.

    Idempotency key: ``(document_id, window_id, prompt_version)`` — re-running the same
    window under the same prompt is a no-op, while a prompt-version bump re-extracts the
    window (corpus_extraction §4). ``window_id`` is ``"{doc_id}:{start}-{end}"`` and the
    ``window_start``/``window_end`` span is an inclusive, 1-based page range.
    """

    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "window_id",
            "prompt_version",
            name="uq_extraction_run_doc_window_prompt",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("case_documents.id"), index=True, nullable=False
    )
    window_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    window_start: Mapped[int] = mapped_column(sa.Integer, nullable=False)  # inclusive, 1-based
    window_end: Mapped[int] = mapped_column(sa.Integer, nullable=False)  # inclusive, 1-based
    prompt_version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # ExtractionStatus
    error: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    rows_emitted: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    anchors_rejected: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created_at()


class ChronologyRowOverlay(Base, FirmScoped):
    """A paralegal's chronology-row edit — first-class, survives rebuilds, never silently
    dropped (chronology_builder §3).

    Keyed by the encounter it annotates; on reapply the builder compares ``base_hash_at_edit``
    against the freshly rebuilt row to decide the :class:`~app.models.enums.OverlayStatus`.
    """

    __tablename__ = "chronology_row_overlays"
    __table_args__ = (
        UniqueConstraint(
            "matter_id", "encounter_id", name="uq_chronology_overlay_matter_encounter"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("medical_encounters.id"), index=True, nullable=False
    )
    edited_fields: Mapped[dict] = mapped_column(sa.JSON, nullable=False, default=dict)
    base_hash_at_edit: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False)  # OverlayStatus
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


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
    # How the flag was produced (# FlagDetector). Server-default "label" in the migration so
    # the not-null ADD succeeds on any existing rows; the ORM default is FlagDetector.LABEL.
    detector: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default="label", server_default="label"
    )
    anchors: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    detail: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    disposition: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)  # FlagDisposition
    disposition_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
    # Actor's role at disposition time — an audit denormalization for fast display/filtering;
    # the GateRecord (actor_id + actor_role) remains the authoritative disposition audit trail.
    disposition_role: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)  # UserRole
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
    # Deterministic source key (e.g. "encounter:<uuid>", "amt:<ledger key>") that makes registry
    # sync idempotent — re-syncing the same upstream fact resolves to the same token slot.
    source_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True, index=True)
    # AMT-only linkage back to the money engine (fact_registry §3): the ledger emission payload,
    # the snapshot value in integer cents (money discipline), and the ledger hash it was minted at.
    ledger_ref: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    snapshot_value_cents: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    ledger_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class RegistryVersion(Base, FirmScoped):
    """One row per registry-version bump for a matter; approvals bind to a version (schema inv 3).

    ``(matter_id, version)`` is unique — a version is minted once. ``frozen`` marks a version
    that a downstream approval has pinned; ``parent_version`` and ``change_reason`` record the
    lineage of why the registry advanced (fact_registry §4).
    """

    __tablename__ = "registry_versions"
    __table_args__ = (
        UniqueConstraint("matter_id", "version", name="uq_registry_version_matter_version"),
    )

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    frozen: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    parent_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    change_reason: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="")
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
    # M4 pull-forward (design D2): MMI is attorney-set at G1.5, never inferred, and the
    # treatment-gap / low-property-damage detectors read these. Both nullable — an attorney may
    # submit strategy before either is known. property_damage_estimate is integer cents (money).
    mmi_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    property_damage_estimate_cents: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
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
    # G2.5-approve audit denormalization: who approved this plan and when. The GateRecord
    # (actor_id + actor_role + created_at) remains the authoritative approval trail; these are a
    # fast-display denorm on the plan row. Both nullable — set only at approve.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
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
    # Binds this draft to the StrategyPlan version it was drafted from (04 §2 inv 3: a draft is
    # keyed to the exact approved plan). server_default "0" so the not-null ADD backfills any
    # placeholder rows (there are none pre-M5).
    strategy_plan_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    # DraftStatus vocabulary; server_default "drafting" so the ADD/existing-row path has a value.
    status: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default="drafting"
    )  # DraftStatus
    # The strategy memo (Opus) for this draft — an attorney-visible matter artifact shown at
    # G2.5/G3, never sent to the carrier (brain2 §Decisions). server_default "" backfills the
    # not-null ADD; "" means the memo degraded (provider down) or has not been generated yet.
    memo: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class DraftSection(Base, FirmScoped):
    """A section of a demand draft; reaches the matter via its parent draft FK.

    ``body_tokenized`` is the tokens-only section prose (brain2 inv 5 — zero raw names/amounts/
    citations); ``rendered_preview`` is the registry-resolved preview (brain2 inv 11). ``spans``
    are the rendered char-offset spans minted at render time that feed M6 provenance
    click-through.
    """

    __tablename__ = "draft_sections"

    id: Mapped[uuid.UUID] = _pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("demand_drafts.id"), index=True, nullable=False
    )
    section_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    # Renamed from ``content_tokenized`` — the brain2 contract vocabulary field name.
    body_tokenized: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    rendered_preview: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # The registry version this section's tokens were minted/validated against (brain2 §Vocabulary
    # — a section carries its registry_version). server_default "0" backfills the not-null ADD.
    registry_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # SectionValidation; server_default "retry_pending" (a freshly-minted section has not yet
    # passed deterministic validation).
    validation: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, server_default="retry_pending"
    )  # SectionValidation
    # Rendered char-offset spans [{span_id, start, end, token_id (bare)}], minted at render time —
    # feeds M6 provenance click-through. Empty until the section is rendered.
    spans: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    # Letter collation order for this section within its draft.
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # The DrafterPromptSnapshot the section was drafted under — {input_hash, rules_blocks,
    # matter_directives, final_hard_constraints}. This is the judge-symmetry lock (brain2 §4): the
    # compliance judge re-hashes this so it grades the exact snapshot the drafter saw.
    # server_default "{}" backfills the not-null ADD (an empty snapshot = a not-yet-drafted row).
    prompt_snapshot: Mapped[dict] = mapped_column(
        sa.JSON, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = _created_at()


class ComplianceFinding(Base, FirmScoped):
    """A G3-panel finding; reaches the matter via its parent draft FK.

    Carries the section it anchors to (``section_id``), the pinned ``registry_version``, its
    ``severity`` (blocking vs advisory) + ``bucket`` (mechanical vs semantic), the anchors the
    attorney sees (compliance inv 11), an optional rendered-text ``span`` for a mechanical splice,
    and the finding ``status``/``disposition`` lifecycle.
    """

    __tablename__ = "compliance_findings"

    id: Mapped[uuid.UUID] = _pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("demand_drafts.id"), index=True, nullable=False
    )
    # The section this finding anchors to. server_default "" backfills the not-null ADD; a
    # draft-level finding (no single section) carries the empty string.
    section_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, server_default="")
    # The pinned registry version the finding was raised against (compliance: a registry-version
    # mismatch is itself a hard block). server_default "0" backfills the not-null ADD.
    registry_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    check_kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)  # CheckKind
    bucket: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # FindingBucket
    # Renamed from ``gating`` — the compliance-contract field name is ``severity``. The enum CLASS
    # stays ``FindingGating``; its VALUES ({blocking, advisory}) are this column's vocabulary.
    severity: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="blocking"
    )  # FindingGating values
    detail: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    # Anchors the attorney sees (compliance inv 11 — what the attorney sees, not a paraphrase).
    anchors: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    # {start, end} into the section's rendered text for a mechanical span-patch splice; nullable
    # (a semantic/regen finding has no splice span).
    span: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    # FindingStatus lifecycle: open -> (patched | regenerated) -> re_verified -> dispositioned.
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="open"
    )  # FindingStatus
    # FindingDisposition ({accept, override}); nullable until dispositioned.
    disposition: Mapped[str | None] = mapped_column(  # FindingDisposition
        sa.String(16), nullable=True
    )
    disposition_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
    override_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------------------
# Audit + gates
# --------------------------------------------------------------------------------------


class GateRecord(Base, FirmScoped):
    """One row per gate action (invariant 9). Idempotent on (matter, idempotency_key).

    M3 Wave B pins client-minted idempotency: the key is unique **per matter** (not per
    (matter, gate)), so a duplicate submit anywhere on the matter replays the first outcome
    (design D3). The service (``orchestrator.service.apply_gate_action``) looks a replay up by
    ``(matter_id, idempotency_key)``, so the DB constraint keys on exactly that pair — the M0
    ``(matter, gate, key)`` shape was superseded here to match the replay semantics.
    """

    __tablename__ = "gate_records"
    __table_args__ = (
        UniqueConstraint("matter_id", "idempotency_key", name="uq_gate_record_matter_idempotency"),
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
    """An exhibit collating pages of a source document (package_builder §3).

    One row per (matter, document) at v1 — a pick is per-document with page lists. Pages are
    tri-state: those in ``include_pages`` collate into the binder; those in ``excluded_pages``
    are explicitly dropped; a page in NEITHER list is "not yet decided". Bates numbering is M5
    and is not on this row yet.
    """

    __tablename__ = "exhibits"
    __table_args__ = (
        UniqueConstraint("matter_id", "document_id", name="uq_exhibit_matter_document"),
    )

    id: Mapped[uuid.UUID] = _pk()
    # Table is empty pre-M4, so the not-null FK is a plain ADD in migration 0006 (no backfill).
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("case_documents.id"), index=True, nullable=False
    )
    exhibit_no: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # Pages that collate into the binder (only these). Absence of a page here is not exclusion.
    include_pages: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    # Pages explicitly dropped. A page in neither include_pages nor here is "not yet decided".
    excluded_pages: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    # Third-party-PHI disposition (# PhiDisposition); "pending" blocks the M5 binder build.
    phi_disposition: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="pending", server_default="pending"
    )
    # Manifest collation order — collation order == index order across a matter's exhibits.
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ArtifactSet(Base, FirmScoped):
    """One immutable build of the demand package, keyed by (draft, registry) version —
    a rebuild after drift is a NEW set, never an overwrite (package_builder §3).

    ``artifacts`` is the manifest of what was built and stored — a list of
    ``{kind, object_key, sha256, byte_count}`` dicts (``kind`` is an ``ArtifactKind`` value). The
    unique ``(matter_id, draft_version, registry_version)`` triple makes a re-request for the same
    approved state resolve to the existing set (idempotent build): the bytes are derivable purely
    from the approved state (inv 10), so the same versions always describe the same package.
    ``created_at`` uses the DB default — it is NOT part of the artifact bytes (those pin their own
    metadata timestamps for byte determinism), so a wall-clock row timestamp is fine here.
    """

    __tablename__ = "artifact_sets"
    __table_args__ = (
        UniqueConstraint(
            "matter_id",
            "draft_version",
            "registry_version",
            name="uq_artifact_set_matter_versions",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("matters.id"), index=True, nullable=False
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("demand_drafts.id"), index=True, nullable=False
    )
    draft_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    registry_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # [{kind, object_key, sha256, byte_count}] — one entry per built artifact (ArtifactKind value).
    artifacts: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    built_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.id"), nullable=True
    )
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
