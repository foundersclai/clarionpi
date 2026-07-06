"""In-house session auth — argon2 password hashing + opaque server-side sessions (M3 Wave A).

The M3 design pins **lean in-house session auth** over an auth framework (ADR-0004): a captive
firm needs no registration/verification machinery, the ``FirmScoped`` ``User`` model already
exists, and this keeps a single small door the rest of the app depends on. The pieces:

* :func:`hash_password` / :func:`verify_password` — argon2 via a module-level
  ``PasswordHasher`` (thread-safe, so one shared instance is correct). ``verify_password`` never
  raises on bad input and evens timing on the no-hash path.
* :func:`create_session` / :func:`resolve_session` / :func:`revoke_session` — the opaque-token
  session table. The cookie carries a raw ``secrets.token_urlsafe`` token; only its sha256 is
  stored, so a DB leak yields nothing usable. Sessions are server-side and revocable (no JWT key
  management — ADR-0004).
* :func:`authenticate` — email+password → user, with failures indistinguishable (no
  user-exists-vs-wrong-password oracle).

Timestamps follow the house naive-UTC convention (see ``corpus/ingest/sessions.py``): the
``DateTime(timezone=True)`` columns round-trip as naive on SQLite, so we store naive UTC and
compare naive-to-naive so SQLite (tests) and Postgres (deploy) agree.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.tenancy import tenant_add
from app.models.orm import AuthSession, User

# Module-level hasher — argon2-cffi's PasswordHasher is thread-safe, so one shared instance is
# both correct and the intended usage.
_ph = PasswordHasher()

# A pre-computed hash of a throwaway value. When verifying against a *missing* hash we still run
# one verify against this so the no-user / no-password path costs about the same wall-clock time
# as a real mismatch — closing a timing side channel that would otherwise reveal "no password set".
_DUMMY_HASH = _ph.hash("clarionpi-timing-evener")


def _utcnow_naive() -> datetime:
    """Naive-UTC now — the house convention (mirrors ``corpus/ingest/sessions.py``).

    The session timestamp columns are ``DateTime(timezone=True)`` but SQLite round-trips them as
    naive, so every app-computed timestamp is stored naive-UTC and compared naive-to-naive.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _sha256_hex(raw: str) -> str:
    """sha256 hexdigest of a raw token — what the session table stores (never the raw token)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """Return an argon2 hash of ``password`` (encodes its own salt + parameters)."""
    return _ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Return whether ``password`` matches ``password_hash``; never raise on bad input.

    ``False`` on a ``None`` hash or any mismatch/corrupt-hash. On the ``None`` path we still burn
    one verify against a pre-computed dummy hash so the timing matches a real mismatch (evening the
    "no password set" side channel — see :data:`_DUMMY_HASH`).
    """
    if password_hash is None:
        try:
            _ph.verify(_DUMMY_HASH, password)
        except Argon2Error:
            pass
        return False
    try:
        return _ph.verify(password_hash, password)
    except Argon2Error:
        return False


def create_session(
    db: Session, *, user: User, ttl_minutes: int | None = None
) -> tuple[AuthSession, str]:
    """Mint a session for ``user`` and return ``(row, raw_token)``.

    Generates an opaque ``secrets.token_urlsafe(32)`` token, stores only its sha256, stamps the
    firm via :func:`~app.core.tenancy.tenant_add`, and commits. The caller sets ``raw_token`` in the
    cookie; it is the only place the raw token ever exists.
    """
    ttl = ttl_minutes if ttl_minutes is not None else get_settings().session_ttl_minutes
    raw_token = secrets.token_urlsafe(32)
    session_row = AuthSession(
        user_id=user.id,
        token_hash=_sha256_hex(raw_token),
        expires_at=_utcnow_naive() + timedelta(minutes=ttl),
    )
    tenant_add(db, session_row, user.firm_id)
    db.commit()
    return session_row, raw_token


def resolve_session(db: Session, *, raw_token: str) -> User | None:
    """Resolve a raw cookie token to its :class:`User`, or ``None`` if it is not usable.

    Looks the token up by sha256 on an **unscoped** session — auth precedes tenancy, so this is the
    bootstrap that *establishes* the firm scope (mirroring the note in ``api.deps``). Returns
    ``None`` if the session is missing, expired, revoked, or its user has since gone.
    """
    row = (
        db.query(AuthSession).filter(AuthSession.token_hash == _sha256_hex(raw_token)).one_or_none()
    )
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at <= _utcnow_naive():
        return None
    return db.get(User, row.user_id)


def revoke_session(db: Session, *, raw_token: str) -> bool:
    """Revoke the session for ``raw_token`` — idempotent. Returns whether a live row was revoked.

    Sets ``revoked_at`` and commits. A missing session, or one already revoked, is a no-op that
    returns ``False`` (so logout is safe to call with no/expired cookie).
    """
    row = (
        db.query(AuthSession).filter(AuthSession.token_hash == _sha256_hex(raw_token)).one_or_none()
    )
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = _utcnow_naive()
    db.commit()
    return True


def authenticate(db: Session, *, email: str, password: str) -> User | None:
    """Return the user for ``email`` if ``password`` verifies, else ``None``.

    Case-insensitive email lookup on an **unscoped** session (auth precedes tenancy). Failure is
    indistinguishable between "no such user" and "wrong password": both return ``None``, and the
    no-user path still runs :func:`verify_password` (which burns a dummy verify) so neither the
    result nor the timing leaks which failed.
    """
    user = db.query(User).filter(User.email.ilike(email)).one_or_none()
    if user is None:
        # Burn a verify against the None path so timing matches the wrong-password case.
        verify_password(password, None)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
