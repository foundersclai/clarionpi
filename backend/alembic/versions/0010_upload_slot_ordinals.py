"""upload_slot_ordinals — stable registration-order pairing key (upload-safety audit, BUS-06)

Hand-written migration. Adds ``upload_slots.ordinal``: the slot's zero-based position in the
client's registration order, the stable key the frontend pairs browser files to slots with
(response-array index pairing silently attached bytes to the wrong declared identity when the
returned order differed from registration order).

Three phases, portable on SQLite (tests/offline) and Postgres (deploy):

1. Add ``ordinal`` as NULLABLE (existing rows have no value yet).
2. Backfill deterministically per session by ``(created_at, id)`` — the exact order every
   pre-ordinal read site used (``sessions.py`` commit, ``uploads.py`` register/resume), so
   existing sessions keep the read order they always had.
3. Batch-alter to NOT NULL and add the ``(session_id, ordinal)`` unique constraint (batch
   because SQLite cannot ALTER/ADD CONSTRAINT in place).

Revision ID: 0010_upload_slot_ordinals
Revises: 0009_artifact_sets
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_upload_slot_ordinals"
down_revision: str | None = "0009_artifact_sets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("upload_slots", sa.Column("ordinal", sa.Integer(), nullable=True))

    # Deterministic backfill: number each session's slots 0..n-1 by (created_at, id) — the
    # pre-ordinal read order — so populated databases keep stable behavior.
    bind = op.get_bind()
    slots = sa.table(
        "upload_slots",
        sa.column("id", sa.Uuid()),
        sa.column("session_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("ordinal", sa.Integer()),
    )
    rows = bind.execute(
        sa.select(slots.c.id, slots.c.session_id).order_by(
            slots.c.session_id, slots.c.created_at, slots.c.id
        )
    ).all()
    counters: dict[object, int] = {}
    for slot_id, session_id in rows:
        ordinal = counters.get(session_id, 0)
        counters[session_id] = ordinal + 1
        bind.execute(sa.update(slots).where(slots.c.id == slot_id).values(ordinal=ordinal))

    with op.batch_alter_table("upload_slots") as batch:
        batch.alter_column("ordinal", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint(
            "uq_upload_slot_session_ordinal", ["session_id", "ordinal"]
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_slots") as batch:
        batch.drop_constraint("uq_upload_slot_session_ordinal", type_="unique")
        batch.drop_column("ordinal")
