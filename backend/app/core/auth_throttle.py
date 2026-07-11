"""Login throttling — DB-backed account + IP failure buckets (auth-hardening audit SEC-04).

Two INDEPENDENT bucket scopes guard the login door: ``account`` (keyed by the canonical
email via :func:`app.models.orm.normalize_email`) and ``ip`` (keyed by the resolved client
IP). A single pair-only email+IP bucket is bypassable by distributing attempts across IPs
or usernames, so both scopes are checked and recorded on every failure. Bucket keys are
keyed-HMAC digests (:data:`Settings.auth_throttle_hmac_secret`) so a DB leak cannot be
dictionary-attacked back into an email/IP list.

Concurrency: bucket creation/increment/lock is one atomic transaction safe under concurrent
workers — row lock via ``SELECT … FOR UPDATE`` with an insert-then-retry on the
``(scope, key_digest)`` unique key, never a lost-update read-modify-write. Buckets are
locked in a stable order (account, then ip) to avoid deadlocks. The clock is injected
(``now``) so window/lockout tests use fixed timestamps.

Client-IP trust: :func:`resolve_client_ip` starts from the immediate peer and honors
``X-Forwarded-For`` ONLY when that peer is inside a configured trusted-proxy CIDR — parsing
validated IP literals right-to-left and selecting the first untrusted hop. This helper is
the SOLE owner of forwarded-client resolution: Uvicorn's ``ProxyHeadersMiddleware`` rewrites
``scope["client"]`` before the app sees it and would destroy the immediate-peer evidence,
so every launch config runs ``--no-proxy-headers`` (see ``Makefile``/``AGENTS.md``,
regression-tested by ``tests/test_launch_config.py``; ADR-0010).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.orm import AuthThrottleBucket, normalize_email

SCOPE_ACCOUNT = "account"
SCOPE_IP = "ip"


class ForwardedChainInvalid(Exception):
    """Raised when a TRUSTED proxy presents a malformed X-Forwarded-For chain.

    Refusing is deliberate: collapsing garbage into a shared "unparseable" bucket would let
    a misconfigured (or hostile) trusted hop alias arbitrary clients together.
    """


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _digest(settings: Settings, scope: str, identifier: str) -> str:
    """Keyed HMAC-SHA256 of a normalized identifier — the only stored form of email/IP."""
    return hmac.new(
        settings.auth_throttle_hmac_secret.encode("utf-8"),
        f"{scope}:{identifier}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _bucket_keys(settings: Settings, *, email: str, client_ip: str) -> list[tuple[str, str]]:
    """The (scope, digest) pairs for a login attempt, in the STABLE lock order."""
    return [
        (SCOPE_ACCOUNT, _digest(settings, SCOPE_ACCOUNT, normalize_email(email))),
        (SCOPE_IP, _digest(settings, SCOPE_IP, client_ip)),
    ]


# ---------------------------------------------------------------------------------------
# Client-IP resolution
# ---------------------------------------------------------------------------------------


def _is_trusted_peer(peer: str, trusted_cidrs: tuple[str, ...]) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False  # non-IP peers (e.g. the TestClient's "testclient") are never proxies
    return any(address in ipaddress.ip_network(cidr) for cidr in trusted_cidrs)


def resolve_client_ip(
    peer_host: str | None,
    forwarded_for_values: list[str],
    trusted_cidrs: tuple[str, ...],
) -> str:
    """Resolve the throttle client identity from the immediate peer + X-Forwarded-For.

    - The immediate peer is authoritative unless it belongs to a trusted-proxy CIDR.
    - Behind a trusted peer, hops are parsed right-to-left as STRICT IP literals; the first
      untrusted hop is the client. A malformed value raises :class:`ForwardedChainInvalid`
      (never collapsed into a shared bucket). If every hop is trusted, the leftmost is used.
    - No header is ever trusted merely because it is present.
    """
    peer = peer_host or "unknown"
    if not _is_trusted_peer(peer, trusted_cidrs):
        return peer
    hops: list[str] = []
    for value in forwarded_for_values:
        hops.extend(part.strip() for part in value.split(",") if part.strip())
    if not hops:
        return peer  # a trusted proxy that forwarded nothing: the peer is the client
    parsed: list[str] = []
    for hop in hops:
        try:
            parsed.append(str(ipaddress.ip_address(hop)))
        except ValueError as exc:
            raise ForwardedChainInvalid("malformed X-Forwarded-For from trusted proxy") from exc
    for hop in reversed(parsed):
        if not _is_trusted_peer(hop, trusted_cidrs):
            return hop
    return parsed[0]  # every hop trusted → the leftmost is the origin client


# ---------------------------------------------------------------------------------------
# Bucket mechanics
# ---------------------------------------------------------------------------------------


def _lock_bucket(db: Session, scope: str, key_digest: str) -> AuthThrottleBucket | None:
    return db.execute(
        select(AuthThrottleBucket)
        .where(AuthThrottleBucket.scope == scope, AuthThrottleBucket.key_digest == key_digest)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _lock_or_create_bucket(
    db: Session, scope: str, key_digest: str, now: datetime
) -> AuthThrottleBucket:
    """Row-lock the bucket, creating it if absent — atomic under concurrent workers.

    Insert-then-retry on the ``(scope, key_digest)`` unique key: two workers racing the
    first failure both end up locking the SAME row (one insert wins; the loser's retry
    selects the winner's row FOR UPDATE), so simultaneous failures cannot undercount.
    """
    bucket = _lock_bucket(db, scope, key_digest)
    if bucket is not None:
        return bucket
    try:
        with db.begin_nested():  # SAVEPOINT: a lost insert race must not kill the txn
            bucket = AuthThrottleBucket(
                scope=scope, key_digest=key_digest, window_started_at=now, failure_count=0
            )
            db.add(bucket)
            db.flush()
        return bucket
    except Exception:  # IntegrityError: another worker inserted first — lock theirs
        db.rollback()
        bucket = _lock_bucket(db, scope, key_digest)
        if bucket is None:  # pragma: no cover - only reachable on storage failure
            raise
        return bucket


def _as_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def check_locked(
    db: Session, *, email: str, client_ip: str, now: datetime | None = None
) -> int | None:
    """Return the Retry-After seconds if EITHER bucket is locked, else ``None``.

    A plain read (no lock): the authoritative increment happens in
    :func:`record_failure`; this pre-check just refuses obviously-locked attempts before
    the password verify burns work.
    """
    settings = get_settings()
    at = now or _utcnow_naive()
    for scope, key_digest in _bucket_keys(settings, email=email, client_ip=client_ip):
        row = db.execute(
            select(AuthThrottleBucket).where(
                AuthThrottleBucket.scope == scope,
                AuthThrottleBucket.key_digest == key_digest,
            )
        ).scalar_one_or_none()
        if row is None or row.locked_until is None:
            continue
        locked_until = _as_naive(row.locked_until)
        if locked_until > at:
            return max(1, int((locked_until - at).total_seconds()))
    return None


def record_failure(db: Session, *, email: str, client_ip: str, now: datetime | None = None) -> None:
    """Record one failed attempt in BOTH buckets and commit — the uniform failure record.

    Runs for known and unknown emails alike (no user-existence oracle). Window semantics:
    a failure outside the window restarts it at count 1; crossing the scope's max failure
    count sets ``locked_until``. All bucket writes + the commit are one transaction.
    """
    settings = get_settings()
    at = now or _utcnow_naive()
    window = timedelta(seconds=settings.auth_login_window_seconds)
    limits = {
        SCOPE_ACCOUNT: settings.auth_login_max_failures_per_account,
        SCOPE_IP: settings.auth_login_max_failures_per_ip,
    }
    try:
        for scope, key_digest in _bucket_keys(settings, email=email, client_ip=client_ip):
            bucket = _lock_or_create_bucket(db, scope, key_digest, at)
            if _as_naive(bucket.window_started_at) + window <= at:
                bucket.window_started_at = at
                bucket.failure_count = 0
            bucket.failure_count += 1
            if bucket.failure_count >= limits[scope]:
                bucket.locked_until = at + timedelta(seconds=settings.auth_login_lockout_seconds)
        db.commit()
    except BaseException:
        db.rollback()
        raise


def clear_account_bucket(db: Session, *, email: str, now: datetime | None = None) -> None:
    """On successful login: clear the ACCOUNT bucket only, and commit.

    The shared IP bucket deliberately survives — one valid login must not erase evidence
    of a password-spraying source.
    """
    settings = get_settings()
    _, account_digest = SCOPE_ACCOUNT, _digest(settings, SCOPE_ACCOUNT, normalize_email(email))
    bucket = _lock_bucket(db, SCOPE_ACCOUNT, account_digest)
    if bucket is not None:
        db.delete(bucket)
    db.commit()


def prune_stale_buckets(db: Session, *, now: datetime | None = None) -> int:
    """Delete buckets whose window AND any lockout are fully in the past; returns count.

    Keeps the pre-auth table bounded. Cheap enough to run opportunistically after a
    successful login; also callable as an ops sweep.
    """
    settings = get_settings()
    at = now or _utcnow_naive()
    horizon = at - timedelta(
        seconds=settings.auth_login_window_seconds + settings.auth_login_lockout_seconds
    )
    stale = db.execute(
        select(AuthThrottleBucket).where(AuthThrottleBucket.window_started_at < horizon)
    ).scalars()
    count = 0
    for bucket in stale:
        locked_until = bucket.locked_until
        if locked_until is not None and _as_naive(locked_until) > at:
            continue  # still locked: keep the evidence until the lock expires
        db.delete(bucket)
        count += 1
    db.commit()
    return count
