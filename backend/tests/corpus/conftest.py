"""Shared fixtures for corpus-layer (M1 ingest) tests.

Provides the in-memory engine, an open session, the seeded dev tenant, a matter in
``corpus_processing``, tmp-dir object storage, and ``make_client`` — a factory that builds a
TestClient around a LOCAL FastAPI app containing just one router. Route tests here must NOT
import ``app.main``: M1 routers are wired into the real app by the Phase-0 wave, and local
apps keep these suites independent of include-order.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db_session, seed_dev_firm_and_user
from app.core.config import Settings, get_settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.models.enums import GateState
from app.models.orm import Firm, Matter, User

FIRM_B_ID = uuid.UUID("00000000-0000-0000-0000-0000000f2b00")


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin APP_ENV=test so any process-global engine/storage default stays out of the repo."""
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
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """One open (unscoped) session for direct-ORM test setup and assertions."""
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def dev_user(db: Session) -> User:
    """The seeded dev attorney (Firm A) attached to the open ``db`` session."""
    return seed_dev_firm_and_user(db)


@pytest.fixture
def matter(db: Session, dev_user: User) -> Matter:
    """A Firm-A matter sitting in ``corpus_processing`` — the M1 ingest entry state."""
    m = Matter(
        client_display_name="Test Client",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    tenant_add(db, m, dev_user.firm_id)
    db.commit()
    return m


@pytest.fixture
def firm_b_matter(db: Session) -> Matter:
    """A matter owned by a different firm — exists to prove cross-tenant isolation (404s)."""
    if db.get(Firm, FIRM_B_ID) is None:
        db.add(Firm(id=FIRM_B_ID, name="Firm B"))
    m = Matter(
        firm_id=FIRM_B_ID,
        client_display_name="B Client",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()
    return m


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "storage")


@pytest.fixture
def make_client(
    session_factory: sessionmaker[Session],
) -> Callable[[APIRouter], TestClient]:
    """Build a TestClient around a local app containing just the given router.

    ``get_db_session`` is overridden to yield from the shared in-memory engine, so requests
    and direct-ORM assertions see one database. Auth remains the M0 dev-attorney stub.
    """

    def _make(router: APIRouter) -> TestClient:
        app = FastAPI()
        app.include_router(router)

        def _override() -> Iterator[Session]:
            s = session_factory()
            try:
                yield s
            finally:
                s.close()

        app.dependency_overrides[get_db_session] = _override
        return TestClient(app)

    return _make
