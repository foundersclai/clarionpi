"""Origin-check CSRF boundary (auth-hardening audit SEC-03).

The conftest ``client`` sends a trusted Origin by default — the same shape a real workbench
request has — so session-mode enforcement stays ON in tests (the production default), and
negative tests strip or replace that header explicitly. Includes the proof that the
middleware protects routes beyond its dedicated probes: an existing authenticated mutation
(logout) fails with ``403 csrf_failed`` once the trusted Origin is removed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import DEV_USER_EMAIL, DEV_USER_PASSWORD, get_db_session, seed_dev_users
from app.core.config import get_settings
from app.main import app

_LOGIN = {"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD}
_TRUSTED = "http://localhost:3400"


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    db = session_factory()
    try:
        seed_dev_users(db)
    finally:
        db.close()
    return session_factory


@pytest.fixture
def session_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTH_MODE", "session")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def bare_client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    """A client with NO default Origin header — the negative-case requester."""

    def _override_db_session() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = _override_db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_unsafe_request_with_no_origin_is_rejected(
    bare_client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    resp = bare_client.post("/api/auth/login", json=_LOGIN)
    assert resp.status_code == 403
    assert resp.json() == {"error": "csrf_failed"}


def test_unsafe_request_with_untrusted_origin_is_rejected(
    bare_client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    resp = bare_client.post(
        "/api/auth/login", json=_LOGIN, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": "csrf_failed"}


@pytest.mark.parametrize("bad_origin", ["null", "not a url", "http://user:pw@localhost:3400"])
def test_unsafe_request_with_malformed_or_null_origin_is_rejected(
    bare_client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    bad_origin: str,
) -> None:
    resp = bare_client.post("/api/auth/login", json=_LOGIN, headers={"Origin": bad_origin})
    assert resp.status_code == 403
    assert resp.json() == {"error": "csrf_failed"}


def test_unsafe_request_with_duplicate_origin_headers_is_rejected(
    bare_client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    resp = bare_client.post(
        "/api/auth/login",
        json=_LOGIN,
        headers=[("Origin", _TRUSTED), ("Origin", _TRUSTED)],
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": "csrf_failed"}


def test_unsafe_request_with_trusted_origin_reaches_the_route(
    bare_client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    resp = bare_client.post("/api/auth/login", json=_LOGIN, headers={"Origin": _TRUSTED})
    assert resp.status_code == 200  # authenticated — the route ran, not the middleware


def test_safe_methods_are_not_rejected(
    bare_client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    # GET without any Origin reaches the route: 401 unauthenticated (auth), never 403 CSRF.
    resp = bare_client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == {"error": "unauthenticated"}


def test_stub_mode_needs_no_origin(bare_client: TestClient, seeded: sessionmaker[Session]) -> None:
    # Stub mode: csrf_enforce defaults False, so local suites keep working with no header.
    resp = bare_client.post("/api/auth/logout")
    assert resp.status_code == 200


def test_existing_authenticated_mutation_fails_without_origin(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    """Beyond the probe: a real authenticated mutation 403s once the Origin is stripped."""
    assert client.post("/api/auth/login", json=_LOGIN).status_code == 200
    stripped = client.post("/api/auth/logout", headers={"Origin": ""})
    assert stripped.status_code == 403
    assert stripped.json() == {"error": "csrf_failed"}
    # With the default trusted Origin back, the same mutation succeeds.
    assert client.post("/api/auth/logout").status_code == 200
