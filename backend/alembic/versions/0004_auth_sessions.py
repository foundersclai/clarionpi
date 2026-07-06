"""auth sessions + user password hash

Hand-written M3 Wave A auth migration. Adds the nullable ``users.password_hash`` column
(argon2 hash; nullable so existing rows and stub-mode seed users need no backfill) and the
``auth_sessions`` table, each mirroring ``app.models.orm`` exactly (same columns, types,
nullability, constraints, indexes). The migration/model-drift test
(``tests/models/test_migration_baseline.py``) reflects a DB built by 0001..0004 and asserts the
reflected schema equals ``Base.metadata``, so this file and the ORM must stay in lockstep.

``password_hash`` is nullable and carries no ``server_default``: it adds no not-null obligation,
so the ALTER succeeds on tables that already hold rows. ``auth_sessions`` defines its unique
constraint on ``token_hash`` inline via the column's ``unique=True`` (portable on both SQLite and
Postgres); no ``batch_alter_table`` is needed.

Revision ID: 0004_auth_sessions
Revises: 0003_extraction_registry
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_auth_sessions"
down_revision: str | None = "0003_extraction_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    # -- password hash on users (nullable, no backfill needed) ---------------------------
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))

    # -- auth sessions -------------------------------------------------------------------
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_firm_id", "auth_sessions", ["firm_id"])


def downgrade() -> None:
    op.drop_table("auth_sessions")
    op.drop_column("users", "password_hash")
