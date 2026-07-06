"""Engine + session factory, derived from :class:`~app.core.config.Settings`.

SQLite specifics: an in-memory URL is process-local *per connection* unless every
connection shares one pool, so in-memory engines use a ``StaticPool`` with
``check_same_thread=False`` — this keeps the schema created by ``create_all_for_tests``
visible to a FastAPI ``TestClient`` running the request on another thread.

Session usage rule: application read paths must go through
:func:`app.core.tenancy.scoped_session`; the bare :func:`get_db_session` dependency exists so
higher layers (``api.deps``) can compose scoping on top of it.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.models.orm import Base


def _engine_kwargs(database_url: str) -> dict[str, object]:
    """SQLite (esp. in-memory) needs a shared static pool + relaxed thread check."""
    if database_url.startswith("sqlite"):
        kwargs: dict[str, object] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
        return kwargs
    return {}


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Build a SQLAlchemy :class:`Engine` from settings (defaults to the process settings)."""
    settings = settings or get_settings()
    return create_engine(
        settings.database_url, future=True, **_engine_kwargs(settings.database_url)
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a :class:`sessionmaker` bound to ``engine`` (autoflush on, no autocommit)."""
    return sessionmaker(bind=engine, autoflush=True, expire_on_commit=False, future=True)


def create_all_for_tests(engine: Engine) -> None:
    """Create every table on ``engine`` — the test/offline substitute for Alembic migrations."""
    Base.metadata.create_all(engine)


# Process-wide engine + factory, built lazily on first request-scope use.
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return (building on first call) the process-wide engine."""
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return (building on first call) the process-wide session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory(get_engine())
    return _session_factory


def get_db_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped :class:`Session`.

    This is the *bare* session; API handlers depend on ``api.deps.get_tenant_session`` which
    layers tenancy scoping on top. The session is closed when the request ends.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
