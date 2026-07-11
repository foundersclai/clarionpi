"""Auth API: session-mode login/logout/me + role guards, and the stub-mode regression.

The api conftest pins ``APP_ENV=test`` and defaults ``AUTH_MODE`` to ``stub``. Session-mode tests
set ``AUTH_MODE=session`` *inside* the test via monkeypatch, clear the settings cache so the app
re-reads it, and rely on monkeypatch to restore the env + a finalizer to clear the cache again — so
no session-mode override leaks into later tests. Users are seeded (with dev passwords) into the same
in-memory engine the ``client`` fixture talks to via ``seed_dev_users``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    DEV_PARALEGAL_EMAIL,
    DEV_USER_EMAIL,
    DEV_USER_PASSWORD,
    _RequireAttorney,
    get_db_session,
    seed_dev_users,
)
from app.core.config import get_settings
from app.main import app
from app.models.orm import AuditEvent, User


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """Seed the three dev users (attorney/paralegal/admin, each with the dev password)."""
    db = session_factory()
    try:
        seed_dev_users(db)
    finally:
        db.close()
    return session_factory


@pytest.fixture
def session_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Switch to real session auth for the duration of a test, then restore.

    ``monkeypatch`` restores ``AUTH_MODE`` on teardown; we clear the settings cache both now (so the
    app reads ``session``) and after (so the next test sees the conftest default again).
    """
    monkeypatch.setenv("AUTH_MODE", "session")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _audit_events(session_factory: sessionmaker[Session]) -> list[AuditEvent]:
    db = session_factory()
    try:
        return db.query(AuditEvent).all()
    finally:
        db.close()


# ------------------------------------------------------------------------------------------
# Session-mode login / me / logout
# ------------------------------------------------------------------------------------------


def test_login_sets_httponly_cookie_and_returns_user(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == DEV_USER_EMAIL
    assert resp.json()["user"]["role"] == "attorney"

    set_cookie = resp.headers["set-cookie"]
    assert "clarionpi_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    # Dev/test posture: the cookie must NOT require Secure (no HTTPS locally) but is rooted
    # at path=/ so logout's deletion matches it.
    assert "secure" not in set_cookie.lower()
    assert "path=/" in set_cookie.lower()

    events = _audit_events(seeded)
    assert any(e.event_kind == "auth" and e.payload.get("event") == "login" for e in events)


def test_login_bad_credentials_returns_typed_401_and_audits(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": DEV_USER_EMAIL, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid_credentials"}

    # The email matched a real user → a login_failed audit row exists (scoped to that firm).
    events = _audit_events(seeded)
    assert any(e.payload.get("event") == "login_failed" for e in events)


def test_me_without_cookie_is_401_unauthenticated(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == {"error": "unauthenticated"}


def test_me_with_cookie_returns_user_and_auth_mode(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    client.post(
        "/api/auth/login",
        json={"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD},
    )
    # TestClient persists the login cookie on the client instance.
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == DEV_USER_EMAIL
    assert body["auth_mode"] == "session"


def test_logout_revokes_session_and_clears_cookie(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    client.post(
        "/api/auth/login",
        json={"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD},
    )
    assert client.get("/api/auth/me").status_code == 200  # authenticated

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}
    # Deletion targets the configured cookie name at the shared path=/ (cookie identity is
    # name+domain+path — a mismatched path would strand the login cookie).
    clearing = logout.headers["set-cookie"].lower()
    assert "clarionpi_session=" in clearing
    assert "path=/" in clearing
    # The revoked session no longer authenticates.
    assert client.get("/api/auth/me").status_code == 401

    events = _audit_events(seeded)
    assert any(e.payload.get("event") == "logout" for e in events)


def test_secure_cookie_round_trip_over_https(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SESSION_COOKIE_SECURE=true (the prod posture), Set-Cookie carries Secure and the
    cookie round-trips over an HTTPS base URL — an HTTP client correctly will NOT resend it."""
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    try:
        # `client` (fixture) installed the DB override on the shared app; this second client
        # reuses it with an HTTPS base URL so httpx agrees to send the Secure cookie back.
        https_client = TestClient(app, base_url="https://testserver")
        resp = https_client.post(
            "/api/auth/login",
            json={"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD},
        )
        assert resp.status_code == 200
        assert "secure" in resp.headers["set-cookie"].lower()
        assert https_client.get("/api/auth/me").status_code == 200
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------------------------------------
# Stub-mode regression — the M0 dev attorney is unchanged
# ------------------------------------------------------------------------------------------


def test_stub_mode_me_returns_dev_attorney(
    client: TestClient, seeded: sessionmaker[Session]
) -> None:
    # No session_mode fixture → AUTH_MODE stays the conftest default (stub). No cookie needed.
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == DEV_USER_EMAIL
    assert body["auth_mode"] == "stub"


# ------------------------------------------------------------------------------------------
# require_role — typed 403 for a forbidden role, 200 for the allowed role
# ------------------------------------------------------------------------------------------


def _probe_client(session_factory: sessionmaker[Session]) -> TestClient:
    """A TestClient over an app carrying a tiny attorney-gated probe endpoint.

    Mounts the probe on the real ``app`` (so it shares the ``get_current_user``/auth wiring) and
    overrides ``get_db_session`` onto the shared in-memory engine, mirroring the conftest client.
    """

    def _override_db_session() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = _override_db_session
    return TestClient(app)


@pytest.fixture
def probe_router() -> Iterator[None]:
    """Register an attorney-gated ``/api/_probe/attorney`` endpoint for the duration of a test."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/api/_probe/attorney", response_model=None)
    def _attorney_only(user: User = _RequireAttorney) -> dict[str, str]:
        return {"ok": "attorney", "user_id": str(user.id)}

    app.include_router(router)
    try:
        yield
    finally:
        # Remove the probe route so it does not leak into other tests.
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/api/_probe/attorney"
        ]


def test_require_role_forbids_paralegal_with_typed_403(
    seeded: sessionmaker[Session], session_mode: None, probe_router: None
) -> None:
    client = _probe_client(seeded)
    try:
        client.post(
            "/api/auth/login",
            json={"email": DEV_PARALEGAL_EMAIL, "password": DEV_USER_PASSWORD},
        )
        resp = client.get("/api/_probe/attorney")
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["error"] == "role_forbidden"
        assert detail["required"] == ["attorney"]
        assert detail["actual"] == "paralegal"
    finally:
        app.dependency_overrides.clear()


def test_require_role_allows_attorney(
    seeded: sessionmaker[Session], session_mode: None, probe_router: None
) -> None:
    client = _probe_client(seeded)
    try:
        client.post(
            "/api/auth/login",
            json={"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD},
        )
        resp = client.get("/api/_probe/attorney")
        assert resp.status_code == 200
        assert resp.json()["ok"] == "attorney"
    finally:
        app.dependency_overrides.clear()
