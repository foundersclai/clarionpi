"""extraction + registry + overlay tables

Hand-written M2 foundations migration. Adds the extraction-run, registry-version, and
chronology-row-overlay tables, plus the new columns on ``medical_encounters`` /
``billing_lines`` / ``fact_tokens``, each mirroring ``app.models.orm`` exactly (same columns,
types, nullability, constraints, indexes). The migration/model-drift test
(``tests/models/test_migration_baseline.py``) reflects a DB built by 0001 + 0002 + 0003 and
asserts the reflected schema equals ``Base.metadata``, so this file and the ORM must stay in
lockstep.

New non-null columns on existing tables carry a ``server_default`` so the ALTER succeeds on
tables that already hold rows: ``medical_encounters.field_confidence`` defaults to an empty
JSON object, and ``billing_lines.reconciliation`` defaults to ``"llm_only"`` (the only
reconciliation status M2 emits). The new tables define their unique constraints inline in
``create_table`` (portable on both SQLite and Postgres); no ``batch_alter_table`` is needed
because the columns added to existing tables carry no new constraints.

Revision ID: 0003_extraction_registry
Revises: 0002_ingest_tables
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_extraction_registry"
down_revision: str | None = "0002_ingest_tables"
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
        onupdate=sa.func.now(),
        nullable=False,
    )


def upgrade() -> None:
    # -- new columns on existing extracted-fact tables -----------------------------------
    # field_confidence is JSON (a per-field score map), NOT a Float column — the Float/money
    # ban is untouched. Empty-object server_default backfills any existing encounter rows.
    op.add_column(
        "medical_encounters",
        sa.Column("field_confidence", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "medical_encounters", sa.Column("merge_basis", sa.String(length=32), nullable=True)
    )
    # M2 emits only "llm_only"; server_default backfills existing billing rows.
    op.add_column(
        "billing_lines",
        sa.Column(
            "reconciliation", sa.String(length=24), nullable=False, server_default="llm_only"
        ),
    )

    # fact_tokens registry-sync + AMT-linkage columns (all nullable, no backfill needed).
    op.add_column("fact_tokens", sa.Column("source_ref", sa.String(length=128), nullable=True))
    op.create_index("ix_fact_tokens_source_ref", "fact_tokens", ["source_ref"])
    op.add_column("fact_tokens", sa.Column("ledger_ref", sa.JSON(), nullable=True))
    op.add_column("fact_tokens", sa.Column("snapshot_value_cents", sa.Integer(), nullable=True))
    op.add_column("fact_tokens", sa.Column("ledger_hash", sa.String(length=64), nullable=True))

    # -- extraction runs -----------------------------------------------------------------
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("case_documents.id"), nullable=False),
        sa.Column("window_id", sa.String(length=128), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("window_end", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("rows_emitted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchors_rejected", sa.Integer(), nullable=False, server_default="0"),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "document_id", "window_id", "prompt_version", name="uq_extraction_run_doc_window_prompt"
        ),
    )
    op.create_index("ix_extraction_runs_matter_id", "extraction_runs", ["matter_id"])
    op.create_index("ix_extraction_runs_document_id", "extraction_runs", ["document_id"])
    op.create_index("ix_extraction_runs_firm_id", "extraction_runs", ["firm_id"])

    # -- registry versions ---------------------------------------------------------------
    op.create_table(
        "registry_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("change_reason", sa.String(length=255), nullable=False, server_default=""),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("matter_id", "version", name="uq_registry_version_matter_version"),
    )
    op.create_index("ix_registry_versions_matter_id", "registry_versions", ["matter_id"])
    op.create_index("ix_registry_versions_firm_id", "registry_versions", ["firm_id"])

    # -- chronology row overlays ---------------------------------------------------------
    op.create_table(
        "chronology_row_overlays",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column(
            "encounter_id", sa.Uuid(), sa.ForeignKey("medical_encounters.id"), nullable=False
        ),
        sa.Column("edited_fields", sa.JSON(), nullable=False),
        sa.Column("base_hash_at_edit", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "matter_id", "encounter_id", name="uq_chronology_overlay_matter_encounter"
        ),
    )
    op.create_index(
        "ix_chronology_row_overlays_matter_id", "chronology_row_overlays", ["matter_id"]
    )
    op.create_index(
        "ix_chronology_row_overlays_encounter_id", "chronology_row_overlays", ["encounter_id"]
    )
    op.create_index("ix_chronology_row_overlays_firm_id", "chronology_row_overlays", ["firm_id"])


def downgrade() -> None:
    op.drop_table("chronology_row_overlays")
    op.drop_table("registry_versions")
    op.drop_table("extraction_runs")

    op.drop_column("fact_tokens", "ledger_hash")
    op.drop_column("fact_tokens", "snapshot_value_cents")
    op.drop_column("fact_tokens", "ledger_ref")
    op.drop_index("ix_fact_tokens_source_ref", table_name="fact_tokens")
    op.drop_column("fact_tokens", "source_ref")

    op.drop_column("billing_lines", "reconciliation")
    op.drop_column("medical_encounters", "merge_basis")
    op.drop_column("medical_encounters", "field_confidence")
