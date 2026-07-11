"""Login-throttle mechanics (auth-hardening audit SEC-04) — fixed clocks, no sleeps.

Service-level coverage: window/lockout arithmetic with an injected ``now``; account and IP
bucket independence (fan-out across IPs hits the account limit, fan-out across emails hits
the IP limit); canonical-email variants sharing one bucket; success clearing ONLY the
account bucket; pruning; and the forwarded-client trust decision as a pure function.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import auth_throttle as throttle
from app.core.config import get_settings
from app.models.orm import AuthThrottleBucket

T0 = datetime(2026, 7, 11, 12, 0, 0)


@pytest.fixture(autouse=True)
def tiny_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Small, readable limits: 3/account, 5/ip, 60s window, 300s lockout."""
    monkeypatch.setenv("AUTH_LOGIN_MAX_FAILURES_PER_ACCOUNT", "3")
    monkeypatch.setenv("AUTH_LOGIN_MAX_FAILURES_PER_IP", "5")
    monkeypatch.setenv("AUTH_LOGIN_WINDOW_SECONDS", "60")
    monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_SECONDS", "300")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _buckets(db: Session, scope: str) -> list[AuthThrottleBucket]:
    return list(
        db.execute(select(AuthThrottleBucket).where(AuthThrottleBucket.scope == scope)).scalars()
    )


def test_account_lock_after_threshold_and_retry_after(session: Session) -> None:
    for i in range(3):
        assert throttle.check_locked(session, email="a@x.com", client_ip="1.2.3.4", now=T0) is None
        throttle.record_failure(session, email="a@x.com", client_ip="1.2.3.4", now=T0)
        del i
    retry = throttle.check_locked(session, email="a@x.com", client_ip="1.2.3.4", now=T0)
    assert retry == 300  # locked for the configured lockout
    # After the lockout expires the account may try again.
    later = T0 + timedelta(seconds=301)
    assert throttle.check_locked(session, email="a@x.com", client_ip="1.2.3.4", now=later) is None


def test_window_expiry_resets_the_count(session: Session) -> None:
    throttle.record_failure(session, email="a@x.com", client_ip="1.2.3.4", now=T0)
    throttle.record_failure(session, email="a@x.com", client_ip="1.2.3.4", now=T0)
    # Outside the 60s window the counter restarts, so two more failures do NOT lock.
    later = T0 + timedelta(seconds=61)
    throttle.record_failure(session, email="a@x.com", client_ip="1.2.3.4", now=later)
    throttle.record_failure(session, email="a@x.com", client_ip="1.2.3.4", now=later)
    assert throttle.check_locked(session, email="a@x.com", client_ip="1.2.3.4", now=later) is None
    account = _buckets(session, "account")
    assert len(account) == 1 and account[0].failure_count == 2


def test_account_fanout_across_ips_hits_account_limit(session: Session) -> None:
    for i in range(3):
        throttle.record_failure(session, email="a@x.com", client_ip=f"10.0.0.{i}", now=T0)
    # A FOURTH ip is still locked out for this account: the account bucket is IP-blind.
    assert (
        throttle.check_locked(session, email="a@x.com", client_ip="10.0.0.99", now=T0) is not None
    )


def test_ip_fanout_across_emails_hits_ip_limit(session: Session) -> None:
    for i in range(5):
        throttle.record_failure(session, email=f"user{i}@x.com", client_ip="9.9.9.9", now=T0)
    # A SIXTH email from the same source is locked: the IP bucket is account-blind.
    assert (
        throttle.check_locked(session, email="fresh@x.com", client_ip="9.9.9.9", now=T0) is not None
    )
    # ...but that fresh email from a DIFFERENT ip is fine (buckets are independent).
    assert throttle.check_locked(session, email="fresh@x.com", client_ip="8.8.8.8", now=T0) is None


def test_canonical_email_variants_share_one_bucket(session: Session) -> None:
    throttle.record_failure(session, email="  User@X.com ", client_ip="1.1.1.1", now=T0)
    throttle.record_failure(session, email="user@x.COM", client_ip="1.1.1.1", now=T0)
    account = _buckets(session, "account")
    assert len(account) == 1
    assert account[0].failure_count == 2
    # Only HMAC digests are stored — never the raw identifier.
    assert "user" not in account[0].key_digest.lower() or len(account[0].key_digest) == 64


def test_success_clears_account_but_not_ip_bucket(session: Session) -> None:
    throttle.record_failure(session, email="a@x.com", client_ip="1.2.3.4", now=T0)
    throttle.record_failure(session, email="a@x.com", client_ip="1.2.3.4", now=T0)
    throttle.clear_account_bucket(session, email="a@x.com", now=T0)
    assert _buckets(session, "account") == []
    ip = _buckets(session, "ip")
    assert len(ip) == 1 and ip[0].failure_count == 2  # spray evidence survives


def test_prune_removes_expired_keeps_locked(session: Session) -> None:
    throttle.record_failure(session, email="old@x.com", client_ip="2.2.2.2", now=T0)
    # A hand-crafted long-lock row: the defensive keep-while-locked guard must hold even for
    # a lock that outlives the normal window+lockout horizon (e.g. after a config change).
    session.add(
        AuthThrottleBucket(
            scope="account",
            key_digest="f" * 64,
            window_started_at=T0,
            failure_count=3,
            locked_until=T0 + timedelta(seconds=10_000),
        )
    )
    session.commit()

    # Past window+lockout (360s) for both rows: the idle buckets prune, the locked one stays.
    mid = T0 + timedelta(seconds=400)
    removed = throttle.prune_stale_buckets(session, now=mid)
    assert removed == 2  # old@x.com's account + ip buckets
    remaining = list(session.execute(select(AuthThrottleBucket)).scalars())
    assert len(remaining) == 1 and remaining[0].key_digest == "f" * 64

    # Once the long lock expires, the row prunes too.
    throttle.prune_stale_buckets(session, now=T0 + timedelta(seconds=20_000))
    assert list(session.execute(select(AuthThrottleBucket)).scalars()) == []


# ---------------------------------------------------------------------------------------
# Forwarded-client resolution (pure function)
# ---------------------------------------------------------------------------------------

_TRUSTED = ("127.0.0.1/32", "10.0.0.0/8")


def test_direct_peer_is_the_client() -> None:
    assert throttle.resolve_client_ip("203.0.113.5", [], _TRUSTED) == "203.0.113.5"


def test_spoofed_forwarded_for_from_untrusted_peer_is_ignored() -> None:
    got = throttle.resolve_client_ip("203.0.113.5", ["198.51.100.7"], _TRUSTED)
    assert got == "203.0.113.5"  # header present ≠ header trusted


def test_trusted_multi_hop_chain_selects_first_untrusted_from_right() -> None:
    got = throttle.resolve_client_ip("127.0.0.1", ["198.51.100.7, 10.0.0.3"], _TRUSTED)
    assert got == "198.51.100.7"


def test_all_trusted_chain_uses_leftmost() -> None:
    got = throttle.resolve_client_ip("127.0.0.1", ["10.0.0.9, 10.0.0.3"], _TRUSTED)
    assert got == "10.0.0.9"


def test_malformed_chain_from_trusted_peer_is_refused() -> None:
    with pytest.raises(throttle.ForwardedChainInvalid):
        throttle.resolve_client_ip("127.0.0.1", ["not-an-ip"], _TRUSTED)


def test_non_ip_peer_is_bucketed_as_itself() -> None:
    # The TestClient peer is the literal "testclient" — never a trusted proxy, still stable.
    assert throttle.resolve_client_ip("testclient", ["1.2.3.4"], _TRUSTED) == "testclient"


def test_trusted_peer_with_no_forwarded_header_is_the_client() -> None:
    assert throttle.resolve_client_ip("127.0.0.1", [], _TRUSTED) == "127.0.0.1"
