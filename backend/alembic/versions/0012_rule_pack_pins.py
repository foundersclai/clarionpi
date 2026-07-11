"""rule_pack_pins — pin the exact rule pack a matter's work attests to (BUS-02)

Hand-written migration. Adds ``matters.rule_pack_version`` + ``matters.rule_pack_fingerprint``
(the pack version string + the deterministic SHA-256 over the complete validated pack model),
written at matter creation from then on.

DELIBERATELY NO BACKFILL: stamping legacy matters with today's YAML would falsely attest that
their earlier deadline, ledger, and drafting work used today's pack. Legacy rows stay NULL —
the audited-package guard fails closed on a missing pin when enabled, and dev/test (guard off)
keeps working with unpinned legacy matters. Plain nullable ADD COLUMNs, portable on SQLite and
Postgres.

Revision ID: 0012_rule_pack_pins
Revises: 0011_auth_throttle
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_rule_pack_pins"
down_revision: str | None = "0011_auth_throttle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("matters", sa.Column("rule_pack_version", sa.String(32), nullable=True))
    op.add_column("matters", sa.Column("rule_pack_fingerprint", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("matters") as batch:
        batch.drop_column("rule_pack_fingerprint")
        batch.drop_column("rule_pack_version")
