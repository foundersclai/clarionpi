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
from app.models.orm import AuditEvent, AuthThrottleBucket, User


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


def test_login_failures_are_uniform_for_known_and_unknown_emails(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    """Known and unknown emails follow the SAME failure persistence (SEC-04): identical
    401 bodies, NO matched-user-only login_failed audit row (that existence-dependent write
    was removed), and a throttle bucket row for each — the uniform security record."""
    known = client.post(
        "/api/auth/login", json={"email": DEV_USER_EMAIL, "password": "wrong-password"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@nowhere.example", "password": "wrong"}
    )
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json() == {"error": "invalid_credentials"}

    # No matched-user-only audit path: the login_failed AuditEvent kind is gone entirely.
    events = _audit_events(seeded)
    assert not any(e.payload.get("event") == "login_failed" for e in events)

    # Both failures produced throttle rows (2 buckets each: account + shared ip → 3 rows).
    db = seeded()
    try:
        buckets = db.query(AuthThrottleBucket).all()
        assert {b.scope for b in buckets} == {"account", "ip"}
        assert len([b for b in buckets if b.scope == "account"]) == 2
        ip_bucket = next(b for b in buckets if b.scope == "ip")
        assert ip_bucket.failure_count == 2
        # Digested keys only — no raw email/IP anywhere in the row.
        for bucket in buckets:
            assert DEV_USER_EMAIL not in bucket.key_digest
    finally:
        db.close()


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
        https_client = TestClient(
            app,
            base_url="https://testserver",
            headers={"Origin": "http://localhost:3400"},
        )
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
    return TestClient(app, headers={"Origin": "http://localhost:3400"})  # trusted default


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


@pytest.fixture
def three_strikes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Route-level throttle tests use a 3-failure account limit."""
    monkeypatch.setenv("AUTH_LOGIN_MAX_FAILURES_PER_ACCOUNT", "3")
    monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_SECONDS", "120")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_repeated_bad_logins_return_429_with_retry_after(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    three_strikes: None,
) -> None:
    bad = {"email": DEV_USER_EMAIL, "password": "wrong-password"}
    # Bodies stay indistinguishable 401s until the threshold is crossed...
    for _ in range(3):
        resp = client.post("/api/auth/login", json=bad)
        assert resp.status_code == 401
        assert resp.json() == {"error": "invalid_credentials"}
    # ...then the door locks with a typed 429 + a correct Retry-After.
    locked = client.post("/api/auth/login", json=bad)
    assert locked.status_code == 429
    assert locked.json() == {"error": "login_throttled"}
    assert 1 <= int(locked.headers["Retry-After"]) <= 120
    # The RIGHT password is also refused while locked (the throttle gates before verify).
    good = client.post(
        "/api/auth/login", json={"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD}
    )
    assert good.status_code == 429


def test_successful_login_resets_the_account_bucket(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    three_strikes: None,
) -> None:
    bad = {"email": DEV_USER_EMAIL, "password": "wrong-password"}
    for _ in range(2):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    good = client.post(
        "/api/auth/login", json={"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD}
    )
    assert good.status_code == 200
    # The account bucket cleared: two more failures are again plain 401s, not a lockout.
    for _ in range(2):
        assert client.post("/api/auth/login", json=bad).status_code == 401


def test_canonical_email_variants_share_one_login_identity(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    three_strikes: None,
) -> None:
    # Failures under case/whitespace variants aggregate into ONE account bucket...
    for variant in (DEV_USER_EMAIL.upper(), f"  {DEV_USER_EMAIL} ", DEV_USER_EMAIL):
        resp = client.post("/api/auth/login", json={"email": variant, "password": "wrong-password"})
        assert resp.status_code == 401
    locked = client.post(
        "/api/auth/login", json={"email": DEV_USER_EMAIL, "password": "wrong-password"}
    )
    assert locked.status_code == 429
    # ...and a variant spelling logs in fine once unlocked state allows (identity, not bytes).
