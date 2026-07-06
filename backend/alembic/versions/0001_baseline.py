"""baseline schema

Hand-written baseline for the ClarionPI model layer. Every ``op.create_table`` here mirrors
``app.models.orm`` exactly — same columns, types, nullability, constraints, and indexes. The
migration/model-drift test (``tests/models/test_migration_baseline.py``) reflects a DB built
by this migration and asserts the reflected table + column sets equal ``Base.metadata``, so
this file and the ORM must stay in lockstep.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def upgrade() -> None:
    # -- tenancy roots -------------------------------------------------------------------
    op.create_table(
        "firms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        _created_at(),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_users_firm_id", "users", ["firm_id"])

    # -- matter --------------------------------------------------------------------------
    op.create_table(
        "matters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_display_name", sa.String(length=255), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("incident_date", sa.Date(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=64), nullable=False),
        sa.Column("venue_county", sa.String(length=128), nullable=True),
        sa.Column("gate_state", sa.String(length=48), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("sol_candidates", sa.JSON(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_matters_firm_id", "matters", ["firm_id"])

    # -- corpus --------------------------------------------------------------------------
    op.create_table(
        "case_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("source_label", sa.String(length=255), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("dedup_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_case_documents_matter_id", "case_documents", ["matter_id"])
    op.create_index("ix_case_documents_firm_id", "case_documents", ["firm_id"])

    op.create_table(
        "document_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("case_documents.id"), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_source", sa.String(length=16), nullable=False),
        # Float acceptable HERE ONLY — OCR confidence score, not currency.
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("image_ref", sa.String(length=1024), nullable=True),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])
    op.create_index("ix_document_pages_firm_id", "document_pages", ["firm_id"])

    # -- extracted facts -----------------------------------------------------------------
    op.create_table(
        "medical_encounters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("date_of_service", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("facility", sa.String(length=255), nullable=False),
        sa.Column("encounter_type", sa.String(length=64), nullable=False),
        sa.Column("complaints", sa.JSON(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("diagnoses", sa.JSON(), nullable=False),
        sa.Column("procedures", sa.JSON(), nullable=False),
        sa.Column("work_status", sa.String(length=128), nullable=True),
        sa.Column("narrative_tokenized", sa.Text(), nullable=False),
        sa.Column("anchors", sa.JSON(), nullable=False),
        sa.Column("merged_from", sa.JSON(), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_medical_encounters_matter_id", "medical_encounters", ["matter_id"])
    op.create_index("ix_medical_encounters_firm_id", "medical_encounters", ["firm_id"])

    op.create_table(
        "billing_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("date_of_service", sa.Date(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("billed_cents", sa.Integer(), nullable=False),
        sa.Column("adjusted_cents", sa.Integer(), nullable=True),
        sa.Column("paid_cents", sa.Integer(), nullable=True),
        sa.Column("outstanding_cents", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("anchor", sa.JSON(), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_billing_lines_matter_id", "billing_lines", ["matter_id"])
    op.create_index("ix_billing_lines_firm_id", "billing_lines", ["firm_id"])

    op.create_table(
        "incident_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("anchors", sa.JSON(), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_incident_facts_matter_id", "incident_facts", ["matter_id"])
    op.create_index("ix_incident_facts_firm_id", "incident_facts", ["firm_id"])

    # -- risk flags ----------------------------------------------------------------------
    op.create_table(
        "risk_flags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("anchors", sa.JSON(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=True),
        sa.Column("disposition_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("disposition_rationale", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_risk_flags_matter_id", "risk_flags", ["matter_id"])
    op.create_index("ix_risk_flags_firm_id", "risk_flags", ["firm_id"])

    # -- fact registry -------------------------------------------------------------------
    op.create_table(
        "fact_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("display_form", sa.Text(), nullable=False),
        sa.Column("anchors", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "matter_id", "token_id", "registry_version", name="uq_fact_token_matter_id_version"
        ),
    )
    op.create_index("ix_fact_tokens_matter_id", "fact_tokens", ["matter_id"])
    op.create_index("ix_fact_tokens_firm_id", "fact_tokens", ["firm_id"])

    # -- strategy ------------------------------------------------------------------------
    op.create_table(
        "strategy_inputs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False, unique=True),
        sa.Column("liability_theory", sa.Text(), nullable=False),
        sa.Column("injury_framing", sa.Text(), nullable=False),
        sa.Column("emphasis_notes", sa.Text(), nullable=False),
        sa.Column("anchor_amount_cents", sa.Integer(), nullable=True),
        sa.Column("venue_posture", sa.Text(), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_strategy_inputs_matter_id", "strategy_inputs", ["matter_id"])
    op.create_index("ix_strategy_inputs_firm_id", "strategy_inputs", ["firm_id"])

    op.create_table(
        "strategy_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("demand_amount_cents", sa.Integer(), nullable=True),
        sa.Column("demand_type", sa.String(length=16), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("emphasis_directives", sa.JSON(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_strategy_plans_matter_id", "strategy_plans", ["matter_id"])
    op.create_index("ix_strategy_plans_firm_id", "strategy_plans", ["firm_id"])

    # -- demand draft + compliance -------------------------------------------------------
    op.create_table(
        "demand_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        _created_at(),
        _updated_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_demand_drafts_matter_id", "demand_drafts", ["matter_id"])
    op.create_index("ix_demand_drafts_firm_id", "demand_drafts", ["firm_id"])

    op.create_table(
        "draft_sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("draft_id", sa.Uuid(), sa.ForeignKey("demand_drafts.id"), nullable=False),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("content_tokenized", sa.Text(), nullable=False),
        sa.Column("rendered_preview", sa.Text(), nullable=True),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_draft_sections_draft_id", "draft_sections", ["draft_id"])
    op.create_index("ix_draft_sections_firm_id", "draft_sections", ["firm_id"])

    op.create_table(
        "compliance_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("draft_id", sa.Uuid(), sa.ForeignKey("demand_drafts.id"), nullable=False),
        sa.Column("check_kind", sa.String(length=64), nullable=False),
        sa.Column("bucket", sa.String(length=16), nullable=False),
        sa.Column("gating", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("dispositioned", sa.Boolean(), nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=True),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_compliance_findings_draft_id", "compliance_findings", ["draft_id"])
    op.create_index("ix_compliance_findings_firm_id", "compliance_findings", ["firm_id"])

    # -- audit + gates -------------------------------------------------------------------
    op.create_table(
        "gate_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("gate", sa.String(length=48), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "matter_id", "gate", "idempotency_key", name="uq_gate_record_matter_gate_key"
        ),
    )
    op.create_index("ix_gate_records_matter_id", "gate_records", ["matter_id"])
    op.create_index("ix_gate_records_firm_id", "gate_records", ["firm_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_kind", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_audit_events_firm_id", "audit_events", ["firm_id"])

    # -- package + telemetry + budget ----------------------------------------------------
    op.create_table(
        "exhibits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("case_documents.id"), nullable=False),
        sa.Column("exhibit_no", sa.Integer(), nullable=True),
        sa.Column("include_pages", sa.JSON(), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_exhibits_document_id", "exhibits", ["document_id"])
    op.create_index("ix_exhibits_firm_id", "exhibits", ["firm_id"])

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_llm_calls_matter_id", "llm_calls", ["matter_id"])
    op.create_index("ix_llm_calls_firm_id", "llm_calls", ["firm_id"])

    op.create_table(
        "matter_budgets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False, unique=True),
        sa.Column("cap_cents", sa.Integer(), nullable=False),
        sa.Column("spent_cents", sa.Integer(), nullable=False),
        sa.Column("warned", sa.Boolean(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_matter_budgets_matter_id", "matter_budgets", ["matter_id"])
    op.create_index("ix_matter_budgets_firm_id", "matter_budgets", ["firm_id"])


def downgrade() -> None:
    for table in (
        "matter_budgets",
        "llm_calls",
        "exhibits",
        "audit_events",
        "gate_records",
        "compliance_findings",
        "draft_sections",
        "demand_drafts",
        "strategy_plans",
        "strategy_inputs",
        "fact_tokens",
        "risk_flags",
        "incident_facts",
        "billing_lines",
        "medical_encounters",
        "document_pages",
        "case_documents",
        "matters",
        "users",
        "firms",
    ):
        op.drop_table(table)
