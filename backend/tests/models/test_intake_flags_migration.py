"""Focused migration test for ``0014_intake_eligibility_flags`` (WI-2).

Builds a database at revision 0013 (pre-preflight), seeds one matter row, then upgrades to
head and asserts: the four flag columns exist NOT NULL, the pre-existing row BACKFILLS to
``'unknown'`` on every flag (the honest "not asked" answer — never blocks gate progress),
and the kept server default gives a raw post-migration insert the same legacy-safe answer.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_FLAGS = (
    "public_entity_involved",
    "plaintiff_is_minor",
    "wrongful_death",
    "coverage_dispute",
)


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


# Lightweight pre-0014 construct — only the NOT-NULL-without-default columns are supplied.
_MATTERS = sa.table(
    "matters",
    sa.column("id", sa.Uuid()),
    sa.column("firm_id", sa.Uuid()),
    sa.column("client_display_name", sa.String(255)),
    sa.column("claim_type", sa.String(32)),
    sa.column("incident_date", sa.Date()),
    sa.column("jurisdiction", sa.String(64)),
    sa.column("gate_state", sa.String(48)),
    sa.column("registry_version", sa.Integer()),
    sa.column("sol_candidates", sa.JSON()),
)


def test_intake_flags_backfill_unknown_and_default_new_rows(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "intake_flags_migration.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)

    # Build the PRE-preflight schema, then seed one matter with no flag columns at all.
    command.upgrade(cfg, "0013_derived_state_staleness")

    matter_id = uuid.uuid4()
    engine = sa.create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            _MATTERS.insert(),
            {
                "id": matter_id,
                "firm_id": uuid.uuid4(),
                "client_display_name": "Pre-Preflight Client",
                "claim_type": "mva",
                "incident_date": dt.date(2025, 11, 3),
                "jurisdiction": "AZ",
                "gate_state": "evidence_review",
                "registry_version": 2,
                "sol_candidates": [],
            },
        )

    command.upgrade(cfg, "head")

    # Schema: all four columns exist and are NOT NULL.
    inspector = sa.inspect(engine)
    cols = {c["name"]: c for c in inspector.get_columns("matters")}
    for flag in _FLAGS:
        assert flag in cols, f"missing column {flag}"
        assert cols[flag]["nullable"] is False

    # Backfill: the pre-existing row reads 'unknown' on every flag.
    select_flags = sa.text(f"SELECT {', '.join(_FLAGS)} FROM matters WHERE id = :id")
    with engine.connect() as conn:
        row = conn.execute(select_flags, {"id": matter_id.hex}).one()
    assert tuple(row) == ("unknown",) * 4

    # Kept server default: a raw insert that never mentions the flags inherits 'unknown'.
    late_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            _MATTERS.insert(),
            {
                "id": late_id,
                "firm_id": uuid.uuid4(),
                "client_display_name": "Raw Post-Migration Row",
                "claim_type": "mva",
                "incident_date": dt.date(2026, 2, 1),
                "jurisdiction": "AZ",
                "gate_state": "corpus_processing",
                "registry_version": 0,
                "sol_candidates": [],
            },
        )
    with engine.connect() as conn:
        row = conn.execute(select_flags, {"id": late_id.hex}).one()
    assert tuple(row) == ("unknown",) * 4
    engine.dispose()
