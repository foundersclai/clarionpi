"""ingest tables

Hand-written M1 corpus-ingest migration. Adds the upload-session, page-text-history, and
dedup-quarantine tables, and the new columns on ``case_documents`` / ``document_pages``, each
mirroring ``app.models.orm`` exactly (same columns, types, nullability, constraints, indexes).
The migration/model-drift test (``tests/models/test_migration_baseline.py``) reflects a DB
built by 0001 + 0002 and asserts the reflected schema equals ``Base.metadata``, so this file
and the ORM must stay in lockstep.

SQLite cannot ``ALTER ... ADD CONSTRAINT``: the ``document_pages`` unique constraint is added
inside ``op.batch_alter_table`` (batch mode recreates the table on SQLite; a no-op wrapper on
Postgres).

Revision ID: 0002_ingest_tables
Revises: 0001_baseline
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_ingest_tables"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    # -- new columns on existing corpus tables -------------------------------------------
    op.add_column(
        "case_documents",
        sa.Column("filename", sa.String(length=512), nullable=False, server_default=""),
    )
    op.add_column("case_documents", sa.Column("storage_key", sa.String(length=1024), nullable=True))
    # Float acceptable HERE ONLY — classifier confidence score, not currency.
    op.add_column(
        "case_documents", sa.Column("classification_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "case_documents",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "case_documents", sa.Column("failure_reason", sa.String(length=512), nullable=True)
    )

    op.add_column(
        "document_pages",
        sa.Column("zero_text", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Plain Uuid, deliberately NOT a ForeignKey — points at page_texts.id (see orm.py).
    op.add_column("document_pages", sa.Column("active_text_id", sa.Uuid(), nullable=True))
    op.create_index("ix_document_pages_active_text_id", "document_pages", ["active_text_id"])
    # SQLite can't ALTER ADD CONSTRAINT; batch mode recreates the table (no-op on Postgres).
    with op.batch_alter_table("document_pages") as batch:
        batch.create_unique_constraint("uq_document_page_doc_page_no", ["document_id", "page_no"])

    # -- upload sessions -----------------------------------------------------------------
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_upload_sessions_matter_id", "upload_sessions", ["matter_id"])
    op.create_index("ix_upload_sessions_firm_id", "upload_sessions", ["firm_id"])

    op.create_table(
        "upload_slots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("upload_sessions.id"), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("received", sa.Boolean(), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("case_documents.id"), nullable=True),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_upload_slots_session_id", "upload_slots", ["session_id"])
    op.create_index("ix_upload_slots_firm_id", "upload_slots", ["firm_id"])

    # -- page text history ---------------------------------------------------------------
    op.create_table(
        "page_texts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("page_id", sa.Uuid(), sa.ForeignKey("document_pages.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_source", sa.String(length=16), nullable=False),
        # Float acceptable HERE ONLY — OCR confidence score, not currency.
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("engine", sa.String(length=64), nullable=True),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_page_texts_page_id", "page_texts", ["page_id"])
    op.create_index("ix_page_texts_firm_id", "page_texts", ["firm_id"])

    # -- dedup quarantine ----------------------------------------------------------------
    op.create_table(
        "dedup_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("case_documents.id"), nullable=False),
        sa.Column(
            "against_document_id", sa.Uuid(), sa.ForeignKey("case_documents.id"), nullable=True
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("page_hash_matches", sa.JSON(), nullable=False),
        # Float acceptable HERE ONLY — shingle-similarity score, not currency.
        sa.Column("shingle_overlap", sa.Float(), nullable=True),
        sa.Column("resolution", sa.String(length=16), nullable=False),
        sa.Column("resolved_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_dedup_decisions_matter_id", "dedup_decisions", ["matter_id"])
    op.create_index("ix_dedup_decisions_document_id", "dedup_decisions", ["document_id"])
    op.create_index("ix_dedup_decisions_firm_id", "dedup_decisions", ["firm_id"])


def downgrade() -> None:
    op.drop_table("dedup_decisions")
    op.drop_table("page_texts")
    op.drop_table("upload_slots")
    op.drop_table("upload_sessions")

    with op.batch_alter_table("document_pages") as batch:
        batch.drop_constraint("uq_document_page_doc_page_no", type_="unique")
    op.drop_index("ix_document_pages_active_text_id", table_name="document_pages")
    op.drop_column("document_pages", "active_text_id")
    op.drop_column("document_pages", "zero_text")

    op.drop_column("case_documents", "failure_reason")
    op.drop_column("case_documents", "needs_review")
    op.drop_column("case_documents", "classification_confidence")
    op.drop_column("case_documents", "storage_key")
    op.drop_column("case_documents", "filename")
