"""API test harness: a TestClient wired to a shared in-memory engine + seeded firms.

The app's ``get_db_session`` dependency is overridden to yield sessions from a single in-memory
engine created here, so requests and assertions share one database. Firm A (with the seeded dev
user) is the caller's tenant; Firm B + its matter exist only to prove cross-tenant isolation
(a Firm-B matter must 404 for the Firm-A caller). Kept in this package's conftest, not the
top-level one.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import DEV_FIRM_ID, get_db_session, seed_dev_firm_and_user
from app.core.config import Settings, get_settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.main import app
from app.models.enums import GateState
from app.models.orm import Firm, Matter

FIRM_B_ID = uuid.UUID("00000000-0000-0000-0000-0000000f2b00")


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin APP_ENV=test so any process-global engine use stays in-memory (never writes a file)."""
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=2500,
        )
    )
    create_all_for_tests(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def firm_b_matter_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    """Seed the dev firm/user (Firm A) plus a separate Firm B owning one matter."""
    db = session_factory()
    try:
        seed_dev_firm_and_user(db)  # Firm A + dev attorney
        db.add(Firm(id=FIRM_B_ID, name="Firm B"))
        matter = Matter(
            firm_id=FIRM_B_ID,
            client_display_name="B Client",
            claim_type="mva",
            incident_date=dt.date(2026, 1, 15),
            jurisdiction="AZ",
            gate_state=GateState.CORPUS_PROCESSING.value,
            registry_version=0,
            sol_candidates=[],
        )
        db.add(matter)
        db.commit()
        return matter.id
    finally:
        db.close()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    """A TestClient whose ``get_db_session`` yields from the shared in-memory engine."""

    def _override_db_session() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = _override_db_session
    # Plain TestClient (no context manager) so startup/shutdown events don't run — the real
    # startup seed targets the process engine, not this test engine; fixtures seed explicitly.
    # The default trusted Origin mirrors the browser: session-mode CSRF enforcement stays ON
    # in tests (same default as production) and suites pass by sending what a real workbench
    # request sends. Negative CSRF tests override or strip this header explicitly.
    try:
        yield TestClient(app, headers={"Origin": "http://localhost:3400"})
    finally:
        app.dependency_overrides.clear()


__all__ = ["DEV_FIRM_ID", "FIRM_B_ID", "client", "engine", "firm_b_matter_id", "session_factory"]
