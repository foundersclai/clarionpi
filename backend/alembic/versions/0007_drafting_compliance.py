"""drafting + compliance reshape (draft/section/finding to the M5 contract shapes)

Hand-written M5 Wave A foundations migration. Mirrors ``app.models.orm`` exactly so the
migration/model-drift test (``tests/models/test_migration_baseline.py`` — reflects a DB built by
0001..0007 and asserts the reflected schema equals ``Base.metadata``) stays green.

**These four tables are pre-M5 PLACEHOLDERS with NO production data** — no producer has written
``demand_drafts`` / ``draft_sections`` / ``compliance_findings`` / (the new columns on)
``strategy_plans`` before this wave (Brain-2, the G3 panel, and G2.5-approve side effects all land
in later M5 waves). A column RENAME or a boolean DROP is therefore a clean reshape here: there is
nothing to backfill and no data to lose. The reshape gives the tables the
``docs/module_contracts/{brain2,compliance}.md`` Vocabulary shapes the five follow-on waves build
against.

Four tables change:

1. ``strategy_plans`` gains the G2.5-approve audit denorm ``approved_by`` (Uuid FK users.id,
   nullable) + ``approved_at`` (DateTime(tz), nullable). The GateRecord stays authoritative.

2. ``demand_drafts`` gains ``strategy_plan_version`` (Integer, not-null, ``server_default="0"``)
   binding the draft to its approved StrategyPlan version (04 §2 inv 3), and its ``status`` column
   picks up a ``server_default="drafting"`` (DraftStatus vocabulary).

3. ``draft_sections`` is reshaped to the brain2 shape:
     * RENAME ``content_tokenized`` -> ``body_tokenized`` (contract vocabulary).
     * ADD ``registry_version`` (Integer, not-null, ``server_default="0"``).
     * ADD ``validation`` (String(24), not-null, ``server_default="retry_pending"``;
       SectionValidation).
     * ADD ``spans`` (JSON, not-null, ``server_default="[]"``) — rendered char-offset spans for M6
       provenance click-through.
     * ADD ``sort_order`` (Integer, not-null, ``server_default="0"``).

4. ``compliance_findings`` is reshaped to the compliance shape:
     * ADD ``section_id`` (String(64), not-null, ``server_default=""``).
     * ADD ``registry_version`` (Integer, not-null, ``server_default="0"``).
     * RENAME ``gating`` -> ``severity`` (String(16), not-null; the contract's field name — the
       enum class stays ``FindingGating``, its VALUES are the vocabulary).
     * ADD ``anchors`` (JSON, not-null, ``server_default="[]"``).
     * ADD ``span`` (JSON, nullable) — {start, end} for a mechanical splice.
     * ADD ``status`` (String(16), not-null, ``server_default="open"``; FindingStatus).
     * ADD ``disposition`` (String(16), nullable; FindingDisposition).
     * ADD ``disposition_by`` (Uuid FK users.id, nullable).
     * DROP the boolean ``dispositioned`` (subsumed by ``status`` + ``disposition``).

SQLite cannot ``ALTER TABLE`` a rename/drop, so the ``draft_sections`` /
``compliance_findings`` reshapes run inside a ``batch_alter_table`` (table-rebuild) — portable on
both SQLite (tests/offline) and Postgres (deploy).

Revision ID: 0007_drafting_compliance
Revises: 0006_risk_exhibits
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_drafting_compliance"
down_revision: str | None = "0006_risk_exhibits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- strategy_plans: G2.5-approve audit denorm (GateRecord stays authoritative) -------
    # The FK column goes in a batch: SQLite refuses to ALTER-add an FK constraint outside a
    # table-rebuild. Name the FK explicitly (batch mode requires named constraints).
    with op.batch_alter_table("strategy_plans") as batch:
        batch.add_column(
            sa.Column(
                "approved_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", name="fk_strategy_plans_approved_by"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))

    # -- demand_drafts: bind draft -> plan version; status gets a DraftStatus default -----
    op.add_column(
        "demand_drafts",
        sa.Column("strategy_plan_version", sa.Integer(), nullable=False, server_default="0"),
    )
    # Re-emit ``status`` with a server_default so the not-null column has a value on any placeholder
    # row and matches the ORM's ``server_default="drafting"``. A plain ALTER of the default is fine
    # on Postgres; under SQLite the batch table-rebuild carries the new default.
    with op.batch_alter_table("demand_drafts") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default="drafting",
        )

    # -- draft_sections: reshape placeholder -> brain2 shape ------------------------------
    # RENAME + ADDs in one batch so SQLite's table-rebuild carries them all.
    with op.batch_alter_table("draft_sections") as batch:
        batch.alter_column(
            "content_tokenized",
            new_column_name="body_tokenized",
            existing_type=sa.Text(),
            existing_nullable=False,
        )
        batch.add_column(
            sa.Column("registry_version", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "validation",
                sa.String(length=24),
                nullable=False,
                server_default="retry_pending",
            )
        )
        batch.add_column(sa.Column("spans", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))

    # -- compliance_findings: reshape placeholder -> compliance shape --------------------
    # RENAME (gating->severity), ADDs, and the boolean DROP in one batch.
    with op.batch_alter_table("compliance_findings") as batch:
        batch.add_column(
            sa.Column("section_id", sa.String(length=64), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("registry_version", sa.Integer(), nullable=False, server_default="0")
        )
        batch.alter_column(
            "gating",
            new_column_name="severity",
            existing_type=sa.String(length=16),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("anchors", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("span", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open")
        )
        batch.add_column(sa.Column("disposition", sa.String(length=16), nullable=True))
        batch.add_column(
            sa.Column(
                "disposition_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", name="fk_compliance_findings_disposition_by"),
                nullable=True,
            )
        )
        batch.drop_column("dispositioned")


def downgrade() -> None:
    # -- compliance_findings: restore the placeholder shape ------------------------------
    with op.batch_alter_table("compliance_findings") as batch:
        batch.add_column(
            sa.Column(
                "dispositioned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.drop_column("disposition_by")
        batch.drop_column("disposition")
        batch.drop_column("status")
        batch.drop_column("span")
        batch.drop_column("anchors")
        batch.alter_column(
            "severity",
            new_column_name="gating",
            existing_type=sa.String(length=16),
            existing_nullable=False,
        )
        batch.drop_column("registry_version")
        batch.drop_column("section_id")

    # -- draft_sections: restore the placeholder shape -----------------------------------
    with op.batch_alter_table("draft_sections") as batch:
        batch.drop_column("sort_order")
        batch.drop_column("spans")
        batch.drop_column("validation")
        batch.drop_column("registry_version")
        batch.alter_column(
            "body_tokenized",
            new_column_name="content_tokenized",
            existing_type=sa.Text(),
            existing_nullable=False,
        )

    # -- demand_drafts: drop the plan-version bind; restore status without a default -----
    with op.batch_alter_table("demand_drafts") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default=None,
        )
    op.drop_column("demand_drafts", "strategy_plan_version")

    # -- strategy_plans: drop the approve denorm -----------------------------------------
    # Batch mode: dropping the FK-bearing ``approved_by`` under SQLite needs a table-rebuild.
    with op.batch_alter_table("strategy_plans") as batch:
        batch.drop_column("approved_at")
        batch.drop_column("approved_by")
