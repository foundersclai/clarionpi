"""Auth routes — login / logout / me (M3 Wave A).

Thin by design (api_and_wire §4): validate the request, do auth work through
:mod:`app.core.auth`, set/clear the session cookie, return a small view. Login + logout run in
**both** auth modes, so a stub-mode backend can still exercise a real seeded-password login and let
the FE develop against it; ``me`` resolves the current user via the shared ``get_current_user``
dependency, which works in both modes.

The request bodies are defined **locally** here (not in ``models/schemas.py``): they are wire-only
shapes used by nothing else, and ``extra="forbid"`` closes them so unexpected fields are rejected.
Email is typed ``str`` (not ``EmailStr``) deliberately — ``email-validator`` is not a dependency,
and login must not 500 on a syntactically odd address; a bad address simply fails to match a user
and returns the same ``401`` as a wrong password.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import auth as auth_core
from app.core import auth_throttle
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.db import get_db_session
from app.models.orm import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Module-level dependency singletons (ruff B008; evaluated once). ``login``/``logout`` use the bare
# session — auth precedes tenancy, so scoping is not yet established at the door.
_DbSession = Depends(get_db_session)
_CurrentUser = Depends(get_current_user)


class LoginRequest(BaseModel):
    """Login body — wire-only, closed. Email is ``str`` (see module doc: no email-validator dep)."""

    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


def _user_public(user: User) -> dict[str, str]:
    """The public user shape returned to the FE — never includes the password hash."""
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }


def _resolve_client_ip(request: Request) -> str:
    """Resolve the throttle client identity via the app-owned trust helper (SEC-04)."""
    settings = get_settings()
    peer_host = request.client.host if request.client is not None else None
    return auth_throttle.resolve_client_ip(
        peer_host,
        request.headers.getlist("x-forwarded-for"),
        settings.auth_trusted_proxy_cidrs,
    )


@router.post("/login", response_model=None)
def login(
    body: LoginRequest,
    request: Request,
    session: Session = _DbSession,
) -> JSONResponse:
    """Authenticate, mint a session, set the HttpOnly cookie; return the user or a typed ``401``.

    Throttled (SEC-04): independent account + IP failure buckets are checked BEFORE the
    password verify (``429 login_throttled`` + ``Retry-After`` when locked) and BOTH record
    every failure — known and unknown emails follow the SAME persistence path, so neither
    the response, the timing, nor the database work reveals whether the user exists. The
    uniform throttle row is the failure security record; there is deliberately NO
    matched-user-only audit write in the failure path.
    """
    settings = get_settings()
    try:
        client_ip = _resolve_client_ip(request)
    except auth_throttle.ForwardedChainInvalid:
        # A TRUSTED proxy sent garbage — refuse rather than mis-bucket the client.
        return JSONResponse(status_code=400, content={"error": "invalid_forwarded_chain"})

    retry_after = auth_throttle.check_locked(session, email=body.email, client_ip=client_ip)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={"error": "login_throttled"},
            headers={"Retry-After": str(retry_after)},
        )

    user = auth_core.authenticate(session, email=body.email, password=body.password)
    if user is None:
        auth_throttle.record_failure(session, email=body.email, client_ip=client_ip)
        return JSONResponse(status_code=401, content={"error": "invalid_credentials"})

    # Success: the account bucket clears (the shared IP bucket survives — spray evidence),
    # and stale buckets are pruned opportunistically so the pre-auth table stays bounded.
    auth_throttle.clear_account_bucket(session, email=body.email)
    auth_throttle.prune_stale_buckets(session)

    _row, raw_token = auth_core.create_session(session, user=user)
    record_event(
        session,
        firm_id=user.firm_id,
        actor_id=user.id,
        event_kind="auth",
        payload={"event": "login", "user_id": str(user.id)},
    )
    session.commit()
    resp = JSONResponse(status_code=200, content={"user": _user_public(user)})
    resp.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        path="/",  # explicit: cookie identity is name+domain+path — must match logout
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_minutes * 60,
        secure=settings.session_cookie_secure,  # env-derived; True in prod (SEC-02)
    )
    return resp


@router.post("/logout", response_model=None)
def logout(
    request: Request,
    session: Session = _DbSession,
) -> JSONResponse:
    """Revoke the cookie's session (if any), clear the cookie, return ``200``.

    Idempotent: with no/expired cookie it is a no-op ``{"ok": true}``. When a live session was
    actually revoked we audit a ``logout`` event scoped to that session's user firm.
    """
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        user = auth_core.resolve_session(session, raw_token=raw_token)
        revoked = auth_core.revoke_session(session, raw_token=raw_token)
        if revoked and user is not None:
            record_event(
                session,
                firm_id=user.firm_id,
                actor_id=user.id,
                event_kind="auth",
                payload={"event": "logout", "user_id": str(user.id)},
            )
            session.commit()
    resp = JSONResponse(status_code=200, content={"ok": True})
    # Cookie identity is name+domain+path, so the shared path="/" is what guarantees the
    # deletion matches the login cookie; the other attributes keep the policy consistent.
    resp.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
    return resp


@router.get("/me", response_model=None)
def me(user: User = _CurrentUser) -> JSONResponse:
    """Return the current user (both modes) plus the active ``auth_mode``.

    Resolution goes through ``get_current_user``, so in session mode a missing/invalid cookie has
    already produced a typed ``401`` before this body runs; in stub mode it resolves the dev
    attorney (or a valid session cookie's user).
    """
    content = _user_public(user) | {"auth_mode": get_settings().auth_mode}
    return JSONResponse(status_code=200, content=content)
