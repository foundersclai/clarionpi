"""Focused migration test for ``0010_upload_slot_ordinals`` (upload-safety audit, BUS-06).

Builds a database at revision 0009 (pre-ordinal), seeds one session holding several slots —
including two sharing one ``created_at`` so the ``id`` tiebreak is exercised — then upgrades
to head and asserts the deterministic ``(created_at, id)`` backfill, the NOT NULL ordinal,
and the ``(session_id, ordinal)`` unique constraint.
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


# Lightweight table constructs (sa.Uuid binds uuid objects portably on SQLite).
_SESSIONS = sa.table(
    "upload_sessions",
    sa.column("id", sa.Uuid()),
    sa.column("firm_id", sa.Uuid()),
    sa.column("matter_id", sa.Uuid()),
    sa.column("status", sa.String(16)),
    sa.column("ttl_expires_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _pre_ordinal_slots_table() -> sa.TableClause:
    return sa.table(
        "upload_slots",
        sa.column("id", sa.Uuid()),
        sa.column("firm_id", sa.Uuid()),
        sa.column("session_id", sa.Uuid()),
        sa.column("filename", sa.String(512)),
        sa.column("size_bytes", sa.Integer()),
        sa.column("storage_key", sa.String(1024)),
        sa.column("received", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )


def test_ordinal_backfill_is_deterministic_and_constrained(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ordinal_migration.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)

    # Build the PRE-ordinal schema, then seed a session with three ordinal-less slots.
    command.upgrade(cfg, "0009_artifact_sets")

    firm_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    session_id = uuid.uuid4()
    t0 = dt.datetime(2026, 7, 1, 10, 0, 0)
    t1 = dt.datetime(2026, 7, 1, 10, 0, 1)
    # Two slots share t1 — the id must break the tie. Choose ids whose hex order is known.
    id_early = uuid.UUID(int=1)
    id_tie_low = uuid.UUID(int=2)
    id_tie_high = uuid.UUID(int=3)

    engine = sa.create_engine(db_url)
    slots = _pre_ordinal_slots_table()
    with engine.begin() as conn:
        conn.execute(
            _SESSIONS.insert(),
            {
                "id": session_id,
                "firm_id": firm_id,
                "matter_id": matter_id,
                "status": "open",
                "ttl_expires_at": dt.datetime(2099, 1, 1),
                "created_at": t0,
            },
        )
        for slot_id, created_at, name in (
            (id_tie_high, t1, "third.pdf"),
            (id_early, t0, "first.pdf"),
            (id_tie_low, t1, "second.pdf"),
        ):
            conn.execute(
                slots.insert(),
                {
                    "id": slot_id,
                    "firm_id": firm_id,
                    "session_id": session_id,
                    "filename": name,
                    "size_bytes": 1,
                    "storage_key": f"matters/{matter_id}/uploads/{session_id}/{slot_id}/{name}",
                    "received": False,
                    "created_at": created_at,
                },
            )

    command.upgrade(cfg, "head")

    # Backfill: (created_at, id) order → first.pdf 0, second.pdf 1 (tie-low id), third.pdf 2.
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT filename, ordinal FROM upload_slots ORDER BY ordinal")
        ).all()
    assert rows == [("first.pdf", 0), ("second.pdf", 1), ("third.pdf", 2)]

    # Schema: ordinal is NOT NULL and (session_id, ordinal) is uniquely constrained.
    inspector = sa.inspect(engine)
    ordinal_col = next(c for c in inspector.get_columns("upload_slots") if c["name"] == "ordinal")
    assert ordinal_col["nullable"] is False
    uniques = {
        (uc["name"], tuple(uc["column_names"]))
        for uc in inspector.get_unique_constraints("upload_slots")
    }
    assert ("uq_upload_slot_session_ordinal", ("session_id", "ordinal")) in uniques

    # And the constraint actually rejects a duplicate (session_id, ordinal).
    dup = uuid.uuid4()
    with engine.begin() as conn, pytest.raises(sa.exc.IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO upload_slots "
                "(id, firm_id, session_id, ordinal, filename, size_bytes, storage_key, received, "
                "created_at) VALUES (:id, :firm, :sess, 0, 'dup.pdf', 1, 'k', 0, :ts)"
            ),
            {
                "id": dup.hex,
                "firm": firm_id.hex,
                "sess": session_id.hex,
                "ts": t0,
            },
        )
    engine.dispose()
