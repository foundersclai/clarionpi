"""Shared fixtures for core tests — an isolated in-memory SQLite engine per test.

Each test gets its own engine + session factory with the full schema created, so core
behaviors (tenancy scoping, audit immutability, metering) run against real tables without any
network or disk. Kept in this package's conftest (never the top-level one).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.models.enums import UserRole
from app.models.orm import Firm, User


@pytest.fixture
def engine() -> Engine:
    """A fresh in-memory SQLite engine with every table created."""
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
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def make_firm(session: Session, name: str) -> Firm:
    """Insert and return a firm."""
    firm = Firm(id=uuid.uuid4(), name=name)
    session.add(firm)
    session.flush()
    return firm


def make_user(session: Session, firm: Firm, email: str) -> User:
    """Insert and return an attorney user in ``firm``."""
    user = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email=email,
        display_name=email,
        role=UserRole.ATTORNEY.value,
    )
    session.add(user)
    session.flush()
    return user
