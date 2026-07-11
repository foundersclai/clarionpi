"""Postgres concurrency proof: simultaneous login failures cannot undercount (SEC-04).

Marked ``integration``: SQLite cannot exercise the production locking primitive (its
dialect ignores ``FOR UPDATE``), so the row-lock + insert-retry upsert is proven on real
Postgres. Two workers record a failure for the same identity at the same moment (barrier-
released); the bucket must end at failure_count == 2 — a read-modify-write would lose one.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core import auth_throttle as throttle
from app.models.orm import AuthThrottleBucket, Base

pytestmark = pytest.mark.integration


def _require_postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("integration suite needs a postgresql DATABASE_URL (docker compose db)")
    return url


def test_simultaneous_failures_cannot_undercount() -> None:
    engine = sa.create_engine(_require_postgres_url())
    Base.metadata.create_all(engine, checkfirst=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    email = f"race-{uuid.uuid4().hex[:10]}@x.com"
    client_ip = "203.0.113.77"
    now = datetime(2026, 7, 11, 12, 0, 0)
    start = threading.Barrier(2, timeout=30)
    errors: list[BaseException] = []

    def _fail_once() -> None:
        try:
            with factory() as db:
                start.wait()
                throttle.record_failure(db, email=email, client_ip=client_ip, now=now)
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    workers = [threading.Thread(target=_fail_once) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
        assert not worker.is_alive()

    assert errors == []
    from app.core.config import get_settings

    digest = throttle._digest(get_settings(), throttle.SCOPE_ACCOUNT, email)
    with factory() as db:
        bucket = db.execute(
            select(AuthThrottleBucket).where(
                AuthThrottleBucket.scope == "account",
                AuthThrottleBucket.key_digest == digest,
            )
        ).scalar_one()
        assert bucket.failure_count == 2  # both concurrent failures counted — nothing lost
    engine.dispose()
