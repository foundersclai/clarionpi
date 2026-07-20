"""billing_service_end_date — the honest service-PERIOD end on billing lines

Hand-written migration. One nullable ``service_end_date`` (Date) column on ``billing_lines``.

Context: an itemized bill often declares only an overall service PERIOD ("Dates of Service:
March 24 - June 16, 2025") with no per-line date. ``date_of_service`` now holds the period
START (kept NOT NULL so every existing sort/consumer keeps a non-null anchor); this new column
holds the period END, and is NULL for an ordinary single-date line.

- Nullable, no server default: existing rows keep ``NULL`` (they are single-date lines by
  construction — the prior schema could not represent a period at all), which is exactly the
  intended "single date, no distinct end" meaning. No backfill needed.
- Plain ADD COLUMN, portable on SQLite and Postgres.

Revision ID: 0015_billing_service_end_date
Revises: 0014_intake_eligibility_flags
Create Date: 2026-07-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_billing_service_end_date"
down_revision: str | None = "0014_intake_eligibility_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "billing_lines",
        sa.Column("service_end_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("billing_lines") as batch:
        batch.drop_column("service_end_date")
