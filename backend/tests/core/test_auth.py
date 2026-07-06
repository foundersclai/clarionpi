"""Core auth: password hash/verify, opaque session lifecycle, and no-oracle authentication.

Runs against the core conftest's in-memory engine (real tables). Covers the argon2 roundtrip and
its bad-input safety, the create → resolve → revoke session path (with expiry/revocation making a
session unusable), the invariant that a raw token is never stored, and that authentication failure
is indistinguishable between a missing user and a wrong password.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core import auth as auth_core
from app.core.auth import (
    authenticate,
    create_session,
    hash_password,
    resolve_session,
    revoke_session,
    verify_password,
)
from app.models.enums import UserRole
from app.models.orm import AuthSession, User

from .conftest import make_firm, make_user


def _user_with_password(session: Session, email: str, password: str) -> User:
    """Seed an attorney with a real argon2 password hash and return it."""
    firm = make_firm(session, "Firm A")
    user = make_user(session, firm, email)
    user.password_hash = hash_password(password)
    session.flush()
    return user


# ------------------------------------------------------------------------------------------
# Password hashing
# ------------------------------------------------------------------------------------------


def test_hash_verify_roundtrip() -> None:
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"  # not stored plaintext
    assert verify_password("correct horse battery staple", h) is True


def test_verify_wrong_password_is_false() -> None:
    h = hash_password("right-password")
    assert verify_password("wrong-password", h) is False


def test_verify_none_hash_is_false_and_does_not_raise() -> None:
    # A stub-mode user with no password set must never crash verification.
    assert verify_password("anything", None) is False


# ------------------------------------------------------------------------------------------
# Session lifecycle
# ------------------------------------------------------------------------------------------


def test_create_then_resolve_session_returns_user(session: Session) -> None:
    user = _user_with_password(session, "a@firm.test", "pw")
    row, raw_token = create_session(session, user=user)

    resolved = resolve_session(session, raw_token=raw_token)
    assert resolved is not None
    assert resolved.id == user.id
    assert row.user_id == user.id


def test_expired_session_resolves_to_none(session: Session) -> None:
    user = _user_with_password(session, "a@firm.test", "pw")
    row, raw_token = create_session(session, user=user)
    # Freeze the expiry into the past.
    row.expires_at = auth_core._utcnow_naive() - timedelta(minutes=1)
    session.commit()

    assert resolve_session(session, raw_token=raw_token) is None


def test_revoked_session_resolves_to_none(session: Session) -> None:
    user = _user_with_password(session, "a@firm.test", "pw")
    _row, raw_token = create_session(session, user=user)

    assert revoke_session(session, raw_token=raw_token) is True
    assert resolve_session(session, raw_token=raw_token) is None


def test_revoke_is_idempotent(session: Session) -> None:
    user = _user_with_password(session, "a@firm.test", "pw")
    _row, raw_token = create_session(session, user=user)

    assert revoke_session(session, raw_token=raw_token) is True
    # Second revoke is a no-op → False, no error.
    assert revoke_session(session, raw_token=raw_token) is False


def test_resolve_unknown_token_is_none(session: Session) -> None:
    make_firm(session, "Firm A")  # a firm exists but no session for this token
    assert resolve_session(session, raw_token="never-minted") is None


def test_raw_token_is_never_stored(session: Session) -> None:
    user = _user_with_password(session, "a@firm.test", "pw")
    _row, raw_token = create_session(session, user=user)

    stored = session.query(AuthSession).one()
    # The raw token appears in no column; only its sha256 is persisted.
    assert stored.token_hash != raw_token
    assert stored.token_hash == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def test_create_session_stamps_firm(session: Session) -> None:
    user = _user_with_password(session, "a@firm.test", "pw")
    row, _raw = create_session(session, user=user)
    assert row.firm_id == user.firm_id


# ------------------------------------------------------------------------------------------
# authenticate — no user-vs-password oracle
# ------------------------------------------------------------------------------------------


def test_authenticate_success_case_insensitive_email(session: Session) -> None:
    user = _user_with_password(session, "Alice@Firm.test", "s3cret")
    # A different-cased email still authenticates.
    out = authenticate(session, email="alice@firm.TEST", password="s3cret")
    assert out is not None
    assert out.id == user.id


def test_authenticate_failures_are_indistinguishable(session: Session) -> None:
    _user_with_password(session, "alice@firm.test", "s3cret")

    # Wrong password for a real user, and a fully unknown email: both return None (no oracle).
    wrong_password = authenticate(session, email="alice@firm.test", password="nope")
    unknown_user = authenticate(session, email="ghost@firm.test", password="whatever")
    assert wrong_password is None
    assert unknown_user is None


def test_authenticate_none_password_hash_is_none(session: Session) -> None:
    # A user that exists but has no password (stub-mode row) cannot authenticate.
    firm = make_firm(session, "Firm A")
    user = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="nopass@firm.test",
        display_name="No Pass",
        role=UserRole.ATTORNEY.value,
        password_hash=None,
    )
    session.add(user)
    session.flush()

    assert authenticate(session, email="nopass@firm.test", password="anything") is None
