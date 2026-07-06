"""risk-flag detector/role columns + exhibit reshape (matter_id, excluded_pages, phi, order)

Hand-written M4 Wave A foundations migration. Mirrors ``app.models.orm`` exactly so the
migration/model-drift test (``tests/models/test_migration_baseline.py``, which reflects a DB
built by 0001..0006 and asserts the reflected schema equals ``Base.metadata``) stays green.

Two tables change:

1. ``risk_flags`` gains ``detector`` (String(24), not-null, ``server_default="label"`` so the
   ADD succeeds on any existing rows — the ORM default is ``FlagDetector.LABEL``) and
   ``disposition_role`` (String(16), nullable — an audit denormalization of the actor's role at
   disposition time; the ``GateRecord`` stays authoritative).

2. ``exhibits`` is reshaped from its M0 placeholder to the package_builder §3 shape (minus Bates,
   which is M5):
     * ``matter_id`` (Uuid FK matters.id, indexed, not-null). The exhibits table is EMPTY pre-M4
       (no producer wrote it before this wave), so the not-null FK is a plain ADD with no
       ``server_default`` and no backfill — safe on both SQLite and Postgres.
     * ``excluded_pages`` (JSON, not-null, ``server_default="[]"``) — page-level explicit excludes;
       ``include_pages`` is unchanged. A page in neither list is "not yet decided".
     * ``phi_disposition`` (String(16), not-null, ``server_default="pending"``) — "pending" blocks
       the M5 binder build.
     * ``sort_order`` (Integer, not-null, ``server_default="0"``) — manifest collation order.
     * ``updated_at`` (house ``_updated_at()``; ``server_default=now()``).
     * ``UniqueConstraint(matter_id, document_id)`` — one exhibit row per doc per matter at v1.

   SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT``, so the unique constraint is added inside a
   ``batch_alter_table`` (table-rebuild) — portable on both SQLite and Postgres. The new columns
   are added in the same batch so the rebuilt table carries them.

Revision ID: 0006_risk_exhibits
Revises: 0005_gate_service
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_risk_exhibits"
down_revision: str | None = "0005_gate_service"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXHIBIT_UNIQUE = "uq_exhibit_matter_document"


def upgrade() -> None:
    # -- risk_flags: provenance + audit-denormalized disposition role -------------------
    # detector is not-null with a "label" server_default so the ADD backfills existing rows.
    op.add_column(
        "risk_flags",
        sa.Column("detector", sa.String(length=24), nullable=False, server_default="label"),
    )
    op.add_column("risk_flags", sa.Column("disposition_role", sa.String(length=16), nullable=True))

    # -- exhibits: reshape placeholder -> package_builder §3 shape (minus Bates, M5) -----
    # Table is empty pre-M4, so matter_id is a plain not-null FK ADD (no backfill). The new
    # columns + the (matter_id, document_id) unique constraint go in one batch so SQLite's
    # table-rebuild carries them all.
    with op.batch_alter_table("exhibits") as batch:
        # Name the FK explicitly: batch mode rebuilds the whole table under SQLite and refuses
        # to re-emit an unnamed constraint ("Constraint must have a name").
        batch.add_column(
            sa.Column(
                "matter_id",
                sa.Uuid(),
                sa.ForeignKey("matters.id", name="fk_exhibits_matter_id"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("excluded_pages", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column(
                "phi_disposition", sa.String(length=16), nullable=False, server_default="pending"
            )
        )
        batch.add_column(sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch.create_unique_constraint(_EXHIBIT_UNIQUE, ["matter_id", "document_id"])
    op.create_index("ix_exhibits_matter_id", "exhibits", ["matter_id"])


def downgrade() -> None:
    op.drop_index("ix_exhibits_matter_id", table_name="exhibits")
    with op.batch_alter_table("exhibits") as batch:
        batch.drop_constraint(_EXHIBIT_UNIQUE, type_="unique")
        batch.drop_column("updated_at")
        batch.drop_column("sort_order")
        batch.drop_column("phi_disposition")
        batch.drop_column("excluded_pages")
        batch.drop_column("matter_id")

    op.drop_column("risk_flags", "disposition_role")
    op.drop_column("risk_flags", "detector")
