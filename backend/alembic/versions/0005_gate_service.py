"""gate service — strategy MMI/property-damage columns + gate-record idempotency constraint

Hand-written M3 Wave B migration. Two changes, each mirroring ``app.models.orm`` exactly so the
migration/model-drift test (``tests/models/test_migration_baseline.py``, which reflects a DB built
by 0001..0005 and asserts the reflected schema equals ``Base.metadata``) stays green:

1. ``strategy_inputs`` gains ``mmi_date`` (Date) and ``property_damage_estimate_cents`` (Integer
   cents) — both **nullable**, no ``server_default`` (they add no not-null obligation, so the ALTER
   succeeds on tables that already hold rows). M4 pull-forward: MMI is attorney-set at G1.5 and the
   treatment-gap / low-property-damage detectors read these (design D2).

2. ``gate_records`` unique constraint is re-keyed from ``(matter_id, gate, idempotency_key)``
   (uq_gate_record_matter_gate_key, the M0 shape) to ``(matter_id, idempotency_key)``
   (uq_gate_record_matter_idempotency). M3 Wave B pins client-minted idempotency as unique **per
   matter** (design D3): a duplicate key anywhere on the matter replays the first outcome, so the
   constraint must not include ``gate``. SQLite cannot ``ALTER TABLE ... DROP/ADD CONSTRAINT``, so
   this uses ``batch_alter_table`` (table-rebuild) — portable on both SQLite and Postgres.

Revision ID: 0005_gate_service
Revises: 0004_auth_sessions
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_gate_service"
down_revision: str | None = "0004_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_GATE_CONSTRAINT = "uq_gate_record_matter_gate_key"
_NEW_GATE_CONSTRAINT = "uq_gate_record_matter_idempotency"


def upgrade() -> None:
    # -- strategy_inputs: MMI + property-damage columns (nullable, no backfill needed) ---
    op.add_column("strategy_inputs", sa.Column("mmi_date", sa.Date(), nullable=True))
    op.add_column(
        "strategy_inputs",
        sa.Column("property_damage_estimate_cents", sa.Integer(), nullable=True),
    )

    # -- gate_records: re-key the idempotency unique constraint (per matter, drop gate) --
    # batch_alter_table rebuilds the table under SQLite so the constraint swap is portable.
    with op.batch_alter_table("gate_records") as batch:
        batch.drop_constraint(_OLD_GATE_CONSTRAINT, type_="unique")
        batch.create_unique_constraint(_NEW_GATE_CONSTRAINT, ["matter_id", "idempotency_key"])


def downgrade() -> None:
    with op.batch_alter_table("gate_records") as batch:
        batch.drop_constraint(_NEW_GATE_CONSTRAINT, type_="unique")
        batch.create_unique_constraint(
            _OLD_GATE_CONSTRAINT, ["matter_id", "gate", "idempotency_key"]
        )

    op.drop_column("strategy_inputs", "property_damage_estimate_cents")
    op.drop_column("strategy_inputs", "mmi_date")
