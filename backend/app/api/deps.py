"""FastAPI dependencies — auth (M3 Wave A), tenancy composition, request-scoped sessions.

Auth is **mode-gated** (``AUTH_MODE``; ADR-0004). This module is the single door
``get_current_user`` that the whole API depends on; the two modes behind it are:

* ``session`` — real login: the request cookie carries an opaque token resolved against the
  server-side session table (:mod:`app.core.auth`). No cookie / invalid → typed ``401``.
* ``stub`` — the M0 dev-attorney convenience (dev/test default): ``get_current_user`` returns the
  seeded dev attorney with no login, so every pre-M3 test keeps passing. If a *valid* session
  cookie happens to be present in stub mode we prefer its user, which lets the FE develop real
  logins against a stub backend.

``get_tenant_session`` is the composition every handler should depend on: it takes the bare
request session and marks it with :func:`~app.core.tenancy.scoped_session` for the current user's
firm, so handler reads are firm-scoped by construction — now scoped to whichever user auth
resolved.

``require_role`` is the role-guard factory backing invariant 8: it yields a dependency that admits
only the listed roles and otherwise raises a **typed** ``403`` the FE renders inline (no gray-out).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import hash_password, resolve_session
from app.core.config import get_settings
from app.core.db import get_db_session
from app.core.tenancy import scoped_session, tenant_add
from app.models.enums import UserRole
from app.models.orm import Firm, User

# Fixed dev identities so seeding is idempotent across restarts (non-prod only).
DEV_FIRM_ID = uuid.UUID("00000000-0000-0000-0000-0000000f1300")
DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000d3f00")
DEV_FIRM_NAME = "ClarionPI Dev Firm"
DEV_USER_EMAIL = "dev-attorney@clarionpi.local"
DEV_USER_DISPLAY_NAME = "Dev Attorney"

# Extra dev users seeded (non-prod only) so the FE can exercise every role against a stub backend.
DEV_PARALEGAL_ID = uuid.UUID("00000000-0000-0000-0000-0000000d3f01")
DEV_PARALEGAL_EMAIL = "dev-paralegal@clarionpi.local"
DEV_PARALEGAL_DISPLAY_NAME = "Dev Paralegal"
DEV_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000d3f02")
DEV_ADMIN_EMAIL = "dev-admin@clarionpi.local"
DEV_ADMIN_DISPLAY_NAME = "Dev Admin"
# Shared dev password for every seeded dev user. Non-prod only (the seed is refused when
# APP_ENV=prod); documented in .env.example. Never a production credential.
DEV_USER_PASSWORD = "dev-password"

# Module-level dependency singletons — see the note in ``routes/matters.py`` (ruff B008).
_DbSession = Depends(get_db_session)


def seed_dev_firm_and_user(session: Session) -> User:
    """Idempotently ensure the seeded dev firm + attorney exist; return the user.

    Called from the app startup hook (non-prod only) and, defensively, from
    :func:`get_current_user` so tests that skip startup still resolve a user. Uses fixed ids, so
    a second call is a no-op lookup rather than a duplicate insert. In session-auth mode the
    attorney also needs a password — :func:`seed_dev_users` backfills that; this function keeps the
    minimal firm+attorney guarantee the pre-M3 tests rely on.
    """
    user = session.get(User, DEV_USER_ID)
    if user is not None:
        return user
    if session.get(Firm, DEV_FIRM_ID) is None:
        firm = Firm(id=DEV_FIRM_ID, name=DEV_FIRM_NAME)
        session.add(firm)
    user = User(
        id=DEV_USER_ID,
        firm_id=DEV_FIRM_ID,
        email=DEV_USER_EMAIL,
        display_name=DEV_USER_DISPLAY_NAME,
        role=UserRole.ATTORNEY.value,
    )
    # tenant_add stamps + validates firm scoping even for the seed user.
    tenant_add(session, user, DEV_FIRM_ID)
    session.commit()
    return user


def _ensure_dev_user(
    session: Session, *, user_id: uuid.UUID, email: str, display_name: str, role: UserRole
) -> None:
    """Idempotently ensure one seeded dev user (with a dev password) exists on the dev firm."""
    existing = session.get(User, user_id)
    if existing is not None:
        if existing.password_hash is None:
            existing.password_hash = hash_password(DEV_USER_PASSWORD)
        return
    user = User(
        id=user_id,
        firm_id=DEV_FIRM_ID,
        email=email,
        display_name=display_name,
        role=role.value,
        password_hash=hash_password(DEV_USER_PASSWORD),
    )
    tenant_add(session, user, DEV_FIRM_ID)


def seed_dev_users(session: Session) -> None:
    """Seed the three dev users (attorney, paralegal, admin) on the dev firm — **non-prod only**.

    Extends :func:`seed_dev_firm_and_user` so session-mode logins work in dev: the dev attorney
    gets a ``password_hash`` if it had none, and a paralegal + admin are added (all with
    :data:`DEV_USER_PASSWORD`), giving the FE one seeded login per role. Guarded so it can never
    run in production — real users are provisioned there, not seeded.
    """
    if get_settings().app_env == "prod":  # pragma: no cover - guard; prod never seeds
        raise RuntimeError("seed_dev_users must not run in production")
    # Ensure the firm + attorney exist first, then backfill the attorney password + extra roles.
    seed_dev_firm_and_user(session)
    _ensure_dev_user(
        session,
        user_id=DEV_USER_ID,
        email=DEV_USER_EMAIL,
        display_name=DEV_USER_DISPLAY_NAME,
        role=UserRole.ATTORNEY,
    )
    _ensure_dev_user(
        session,
        user_id=DEV_PARALEGAL_ID,
        email=DEV_PARALEGAL_EMAIL,
        display_name=DEV_PARALEGAL_DISPLAY_NAME,
        role=UserRole.PARALEGAL,
    )
    _ensure_dev_user(
        session,
        user_id=DEV_ADMIN_ID,
        email=DEV_ADMIN_EMAIL,
        display_name=DEV_ADMIN_DISPLAY_NAME,
        role=UserRole.ADMIN,
    )
    session.commit()


def get_current_user(request: Request, session: Session = _DbSession) -> User:
    """Return the current user, dispatched on ``AUTH_MODE`` (see module doc).

    The lookup runs on the bare (unscoped) session on purpose: this is the auth bootstrap that
    *establishes* the tenant, so it necessarily precedes firm scoping.
    """
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)

    if settings.auth_mode == "session":
        user = resolve_session(session, raw_token=raw_token) if raw_token else None
        if user is None:
            raise HTTPException(status_code=401, detail={"error": "unauthenticated"})
        return user

    if settings.auth_mode != "stub":
        # Startup validation refuses this, but a hot-mutated env must not silently
        # fall through to the dev-attorney stub (SEC-01: fail closed, never open).
        raise RuntimeError(f"invalid AUTH_MODE {settings.auth_mode!r}; expected stub|session")

    # stub mode: a valid session cookie (if present) wins, so stub-backend FE dev can exercise real
    # logins; otherwise fall back to the seeded dev attorney exactly as at M0.
    if raw_token:
        user = resolve_session(session, raw_token=raw_token)
        if user is not None:
            return user
    return seed_dev_firm_and_user(session)


# Defined after ``get_current_user`` so the singleton can reference it (ruff B008).
_CurrentUser = Depends(get_current_user)


def require_role(*roles: UserRole) -> Callable[[User], User]:
    """Build a dependency that admits only ``roles``, else raises a typed ``403``.

    The refusal body is typed (``role_forbidden`` + ``required`` + ``actual``) so the FE renders the
    authorization reason inline rather than graying the control out (invariant 8; api_and_wire's
    typed-403 contract). Used via the exported ``_RequireAttorney`` / ``_RequireParalegal`` /
    ``_RequireAdmin`` singletons the gate wave depends on.
    """
    allowed = {role.value for role in roles}

    def _dep(user: User = _CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "role_forbidden",
                    "required": [role.value for role in roles],
                    "actual": user.role,
                },
            )
        return user

    return _dep


# Module-level role-guard dependency singletons (ruff B008; evaluated once). Exported for the gate
# wave so route modules attach role guards without rebuilding the factory per signature.
_RequireAttorney = Depends(require_role(UserRole.ATTORNEY))
_RequireParalegal = Depends(require_role(UserRole.PARALEGAL))
_RequireAdmin = Depends(require_role(UserRole.ADMIN))


def get_tenant_session(
    session: Session = _DbSession,
    user: User = _CurrentUser,
) -> Iterator[Session]:
    """Yield the request session, firm-scoped to the current user's firm.

    This is the dependency handlers use for all data access — every SELECT on the yielded session
    is transparently constrained to ``user.firm_id`` (:func:`~app.core.tenancy.scoped_session`),
    so a handler physically cannot read another firm's rows.
    """
    yield scoped_session(session, user.firm_id)
