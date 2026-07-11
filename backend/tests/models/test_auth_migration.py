"""Migration 0011 (canonical email + throttle table) — preflight, backfill, constraints.

The collision preflight must FAIL VISIBLY (with remediation text) on canonical-email
duplicates and modify nothing; a collision-free upgrade backfills through the shared
normalization and lands the NOT NULL + unique constraints. The drift test compares
tables/columns only, so the unique constraints are asserted here explicitly.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


_FIRMS = sa.table("firms", sa.column("id", sa.Uuid()), sa.column("name", sa.String(255)))


def _pre_0011_users_table() -> sa.TableClause:
    return sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("firm_id", sa.Uuid()),
        sa.column("email", sa.String(320)),
        sa.column("display_name", sa.String(255)),
        sa.column("role", sa.String(32)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )


def _seed_pre_0011(db_url: str, emails: list[str]) -> None:
    engine = sa.create_engine(db_url)
    users = _pre_0011_users_table()
    with engine.begin() as conn:
        firm_id = uuid.uuid4()
        conn.execute(_FIRMS.insert(), {"id": firm_id, "name": "Migration Firm"})
        for email in emails:
            conn.execute(
                users.insert(),
                {
                    "id": uuid.uuid4(),
                    "firm_id": firm_id,
                    "email": email,
                    "display_name": email,
                    "role": "attorney",
                    "created_at": dt.datetime(2026, 7, 1),
                },
            )
    engine.dispose()


def test_collision_preflight_fails_visibly_without_modifying_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_url = f"sqlite:///{tmp_path / 'collide.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0010_upload_slot_ordinals")
    _seed_pre_0011(db_url, ["Dup@Example.com", "dup@example.COM"])

    with pytest.raises(RuntimeError, match="canonical-email collision"):
        command.upgrade(cfg, "head")

    # Nothing modified: both users intact, no normalized_email column, revision unchanged.
    engine = sa.create_engine(db_url)
    inspector = sa.inspect(engine)
    assert "normalized_email" not in {c["name"] for c in inspector.get_columns("users")}
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM users")).scalar_one() == 2
        version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0010_upload_slot_ordinals"
    engine.dispose()


def test_collision_free_upgrade_backfills_and_constrains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_url = f"sqlite:///{tmp_path / 'clean.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0010_upload_slot_ordinals")
    _seed_pre_0011(db_url, ["  Alice@Example.com ", "bob@example.com"])

    command.upgrade(cfg, "head")

    engine = sa.create_engine(db_url)
    inspector = sa.inspect(engine)
    # normalized_email: NOT NULL, backfilled via trim+casefold, uniquely constrained.
    column = next(c for c in inspector.get_columns("users") if c["name"] == "normalized_email")
    assert column["nullable"] is False
    with engine.connect() as conn:
        values = set(conn.execute(sa.text("SELECT normalized_email FROM users")).scalars())
    assert values == {"alice@example.com", "bob@example.com"}
    users_uniques = {
        (uc["name"], tuple(uc["column_names"])) for uc in inspector.get_unique_constraints("users")
    }
    assert ("uq_users_normalized_email", ("normalized_email",)) in users_uniques
    # The throttle table exists with its bucket-key unique constraint.
    bucket_uniques = {
        (uc["name"], tuple(uc["column_names"]))
        for uc in inspector.get_unique_constraints("auth_throttle_buckets")
    }
    assert ("uq_auth_throttle_scope_key", ("scope", "key_digest")) in bucket_uniques
    engine.dispose()
