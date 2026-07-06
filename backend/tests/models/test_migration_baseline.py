"""Migration/model drift test — the baseline migration must reproduce ``Base.metadata``.

Runs ``alembic upgrade head`` against a throwaway SQLite file, reflects the result, and
asserts the reflected schema matches the ORM metadata (full table set; column-name sets for
three spot tables). This catches any drift between ``orm.py`` and ``0001_baseline.py``.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.models.orm import Base

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _reflect(db_url: str) -> sa.MetaData:
    engine = sa.create_engine(db_url)
    meta = sa.MetaData()
    meta.reflect(bind=engine)
    engine.dispose()
    return meta


def test_baseline_migration_matches_metadata(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "drift_check.db"
    db_url = f"sqlite:///{db_path}"
    # env.py reads DATABASE_URL; set it too so both online paths agree on the target.
    monkeypatch.setenv("DATABASE_URL", db_url)

    command.upgrade(_alembic_config(db_url), "head")

    reflected = _reflect(db_url)

    expected_tables = set(Base.metadata.tables) | {"alembic_version"}
    assert set(reflected.tables) == expected_tables

    for table_name in ("matters", "fact_tokens", "gate_records"):
        expected_cols = set(Base.metadata.tables[table_name].columns.keys())
        actual_cols = set(reflected.tables[table_name].columns.keys())
        assert actual_cols == expected_cols, f"column drift in {table_name}"


def test_baseline_migration_column_sets_match_all_tables(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "drift_check_all.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    command.upgrade(_alembic_config(db_url), "head")
    reflected = _reflect(db_url)

    for table_name, table in Base.metadata.tables.items():
        expected_cols = set(table.columns.keys())
        actual_cols = set(reflected.tables[table_name].columns.keys())
        assert actual_cols == expected_cols, f"column drift in {table_name}"
