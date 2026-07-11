"""auth_throttle_and_canonical_email — login throttling + global canonical login identity
(auth-hardening audit SEC-04, ADR-0010)

Two pieces:

1. ``users.normalized_email`` — the canonical (trim + casefold) login identity, globally
   unique. Added nullable, backfilled through the SAME normalization the ORM hook uses,
   then batch-altered to NOT NULL + the ``uq_users_normalized_email`` constraint. The
   backfill is preceded by a COLLISION PREFLIGHT: if two existing users share a canonical
   email, the migration FAILS VISIBLY with remediation instructions and modifies nothing —
   it never picks a winner or deletes data.

2. ``auth_throttle_buckets`` — the pre-auth account/IP failure buckets. Deliberately NOT
   firm-scoped (login precedes tenancy — the one sanctioned exception, ADR-0010; the
   tenancy-shape test exempts it by name). Plain create_table (new table).

Revision ID: 0011_auth_throttle
Revises: 0010_upload_slot_ordinals
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_auth_throttle"
down_revision: str | None = "0010_upload_slot_ordinals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize(email: str) -> str:
    """Mirror of ``app.models.orm.normalize_email`` (trim + casefold) — pinned here so the
    migration stays reproducible even if the app helper later changes."""
    return email.strip().casefold()


def upgrade() -> None:
    bind = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("email", sa.String(320)),
        sa.column("normalized_email", sa.String(320)),
    )

    # Collision preflight BEFORE any schema change: fail visibly, modify nothing.
    rows = bind.execute(sa.select(users.c.id, users.c.email)).all()
    seen: dict[str, list[str]] = {}
    for user_id, email in rows:
        seen.setdefault(_normalize(email), []).append(str(user_id))
    collisions = {canon: ids for canon, ids in seen.items() if len(ids) > 1}
    if collisions:
        detail = "; ".join(f"{canon!r}: users {ids}" for canon, ids in sorted(collisions.items()))
        raise RuntimeError(
            "canonical-email collision(s) block this migration — the wire login accepts only "
            "an email, so one canonical email must identify exactly one user (ADR-0010). "
            f"Resolve by changing/removing the duplicate accounts, then re-run: {detail}"
        )

    op.add_column("users", sa.Column("normalized_email", sa.String(320), nullable=True))
    for user_id, email in rows:
        bind.execute(
            sa.update(users).where(users.c.id == user_id).values(normalized_email=_normalize(email))
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("normalized_email", existing_type=sa.String(320), nullable=False)
        batch.create_unique_constraint("uq_users_normalized_email", ["normalized_email"])

    op.create_table(
        "auth_throttle_buckets",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("scope", "key_digest", name="uq_auth_throttle_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("auth_throttle_buckets")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_normalized_email", type_="unique")
        batch.drop_column("normalized_email")
