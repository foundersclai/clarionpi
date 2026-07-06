"""FastAPI dependencies — auth (M0 STUB), tenancy composition, request-scoped sessions.

⚠️  AUTH IS A STUB AT M0.  ``get_current_user`` returns a single seeded dev *attorney* — there is
no login, no password, no TOTP. This is deliberate scaffolding: the real ``fastapi-users`` +
argon2 + TOTP stack lands at M3 (platform_core §1) and *replaces this whole module's auth path*.
Do not build anything that assumes multi-user auth semantics from this stub.

``get_tenant_session`` is the composition every handler should depend on: it takes the bare
request session and marks it with :func:`~app.core.tenancy.scoped_session` for the current user's
firm, so handler reads are firm-scoped by construction.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.db import get_db_session
from app.core.tenancy import scoped_session, tenant_add
from app.models.enums import UserRole
from app.models.orm import Firm, User

# Fixed dev identities so seeding is idempotent across restarts (M0 only; gone at M3).
DEV_FIRM_ID = uuid.UUID("00000000-0000-0000-0000-0000000f1300")
DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000d3f00")
DEV_FIRM_NAME = "ClarionPI Dev Firm"
DEV_USER_EMAIL = "dev-attorney@clarionpi.local"
DEV_USER_DISPLAY_NAME = "Dev Attorney"

# Module-level dependency singletons — see the note in ``routes/matters.py`` (ruff B008).
_DbSession = Depends(get_db_session)


def seed_dev_firm_and_user(session: Session) -> User:
    """Idempotently ensure the seeded dev firm + attorney exist; return the user.

    Called from the app startup hook (non-prod only) and, defensively, from
    :func:`get_current_user` so tests that skip startup still resolve a user. Uses fixed ids, so
    a second call is a no-op lookup rather than a duplicate insert.
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


def get_current_user(session: Session = _DbSession) -> User:
    """Return the current user. **M0 STUB** — always the seeded dev attorney (see module doc).

    The lookup runs on the bare (unscoped) session on purpose: this is the auth bootstrap that
    *establishes* the tenant, so it necessarily precedes firm scoping.
    """
    return seed_dev_firm_and_user(session)


# Defined after ``get_current_user`` so the singleton can reference it (ruff B008).
_CurrentUser = Depends(get_current_user)


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
