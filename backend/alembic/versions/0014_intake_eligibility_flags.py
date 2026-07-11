"""intake_eligibility_flags — the WI-2 pilot-intake preflight flags

Hand-written migration. Four tri-state ``IntakeFlagAnswer`` columns on ``matters``
(``public_entity_involved`` / ``plaintiff_is_minor`` / ``wrongful_death`` /
``coverage_dispute``), each NOT NULL with server default ``'unknown'``:

- Existing rows BACKFILL to ``'unknown'`` — they predate the preflight, and the honest
  answer for them is "not asked". The eligibility rule is a CREATION-TIME check only, so
  a stored ``'unknown'`` never blocks an existing matter's gate progress (plan risk note).
- The server default is KEPT after the backfill: any row inserted outside the create API
  (fixtures, manual SQL) inherits the same legacy-safe answer instead of failing NOT NULL.
  New matters created through the API always carry explicit attorney answers — the create
  request requires all four fields, no silent defaults.

Plain ADD COLUMN ... NOT NULL DEFAULT, portable on SQLite and Postgres.

Revision ID: 0014_intake_eligibility_flags
Revises: 0013_derived_state_staleness
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_intake_eligibility_flags"
down_revision: str | None = "0013_derived_state_staleness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FLAG_COLUMNS = (
    "public_entity_involved",
    "plaintiff_is_minor",
    "wrongful_death",
    "coverage_dispute",
)


def upgrade() -> None:
    for name in _FLAG_COLUMNS:
        op.add_column(
            "matters",
            sa.Column(name, sa.String(8), nullable=False, server_default="unknown"),
        )


def downgrade() -> None:
    with op.batch_alter_table("matters") as batch:
        for name in reversed(_FLAG_COLUMNS):
            batch.drop_column(name)
