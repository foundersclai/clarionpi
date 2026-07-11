"""Postgres row-lock serialization for upload PUT vs commit vs expiry (SEC-05 step 5).

Marked ``integration``: needs the docker-compose Postgres (port 5433) and a
``postgresql+psycopg`` ``DATABASE_URL``. The SQLite unit suite CANNOT prove row locking
(SQLAlchemy's SQLite dialect ignores ``FOR UPDATE``), so the lock protocol's guarantees are
proven here, deterministically — threads coordinate through events/barriers around the
locked section, never sleeps:

1. A re-PUT cannot replace a blob after commit wins the session-row lock.
2. Commit cannot transition midway through a PUT that holds the lock — it serializes after
   and then sees the received slot (without the lock it would raise ``UploadIncomplete``).
3. The expiry sweep skips a row currently locked by an in-flight PUT (``SKIP LOCKED``).
4. An expiry candidate observed before waiting is skipped when commit changes the session
   state before expiry acquires the lock (the under-lock predicate recheck).
"""

from __future__ import annotations

import datetime as dt
import io
import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import IO

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.core.storage import LocalDiskStorage, ObjectStorage, StagedObjectReplacement
from app.corpus.ingest.sessions import (
    UploadSessionNotOpen,
    _utcnow,
    commit_session,
    expire_stale_sessions,
    receive_slot_blob,
    register_upload_session,
)
from app.models.enums import GateState, UploadSessionStatus
from app.models.orm import Base, Firm, Matter, UploadSession, UploadSlot, User
from app.models.schemas import UploadFileDecl

pytestmark = pytest.mark.integration

_WAIT = 30.0  # generous ceiling for event waits; the happy path never approaches it


def _require_postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("integration suite needs a postgresql DATABASE_URL (docker compose db)")
    return url


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[sa.Engine]:
    engine = sa.create_engine(_require_postgres_url())
    Base.metadata.create_all(engine, checkfirst=True)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_sessions(pg_engine: sa.Engine) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    yield factory


@pytest.fixture
def seeded(pg_sessions: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh firm/user/matter per test (random ids — the dev DB is shared)."""
    with pg_sessions() as db:
        firm = Firm(id=uuid.uuid4(), name=f"Concurrency Test Firm {uuid.uuid4().hex[:8]}")
        db.add(firm)
        db.flush()
        user = User(
            firm_id=firm.id,
            email=f"attorney-{uuid.uuid4().hex[:8]}@test.local",
            display_name="Concurrency Attorney",
            role="attorney",
        )
        db.add(user)
        matter = Matter(
            firm_id=firm.id,
            client_display_name="Concurrency Client",
            claim_type="mva",
            incident_date=dt.date(2026, 1, 15),
            jurisdiction="AZ",
            gate_state=GateState.CORPUS_PROCESSING.value,
            registry_version=0,
            sol_candidates=[],
        )
        db.add(matter)
        db.commit()
        return user.id, matter.id


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "storage")


class _GatedStaged:
    """Wrap a staged replacement so promote() can hold inside the locked section."""

    def __init__(self, inner: StagedObjectReplacement, gate: _PromoteGate) -> None:
        self._inner = inner
        self._gate = gate

    def promote(self) -> None:
        self._gate.entered.set()
        assert self._gate.resume.wait(_WAIT), "promote gate never released"
        self._inner.promote()

    def rollback(self) -> None:
        self._inner.rollback()

    def finalize(self) -> None:
        self._inner.finalize()


class _PromoteGate:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.resume = threading.Event()


class _GatedStorage:
    """Storage wrapper with barriers: after staging and/or inside promote (under the lock)."""

    def __init__(
        self,
        inner: ObjectStorage,
        *,
        staged: threading.Event | None = None,
        staged_resume: threading.Event | None = None,
        promote_gate: _PromoteGate | None = None,
    ) -> None:
        self._inner = inner
        self._staged = staged
        self._staged_resume = staged_resume
        self._promote_gate = promote_gate

    def stage_fileobj(self, key: str, fileobj: IO[bytes]) -> StagedObjectReplacement:
        handle = self._inner.stage_fileobj(key, fileobj)
        if self._staged is not None:
            self._staged.set()
        if self._staged_resume is not None:
            assert self._staged_resume.wait(_WAIT), "staged gate never released"
        if self._promote_gate is not None:
            return _GatedStaged(handle, self._promote_gate)
        return handle

    # Delegate the rest of the port.
    def put(self, key: str, data: bytes) -> None:
        self._inner.put(key, data)

    def get(self, key: str) -> bytes:
        return self._inner.get(key)

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)

    def delete(self, key: str) -> None:
        self._inner.delete(key)

    def presign_put(self, key: str) -> str | None:
        return self._inner.presign_put(key)


def _register_one(
    factory: sessionmaker[Session],
    seeded: tuple[uuid.UUID, uuid.UUID],
    storage: LocalDiskStorage,
    *,
    ttl_minutes: int | None = None,
) -> uuid.UUID:
    user_id, matter_id = seeded
    with factory() as db:
        user = db.get(User, user_id)
        matter = db.get(Matter, matter_id)
        assert user is not None and matter is not None
        session = register_upload_session(
            db,
            user=user,
            matter=matter,
            files=[UploadFileDecl(filename="a.pdf", size_bytes=2)],
            storage=storage,
            ttl_minutes=ttl_minutes,
        )
        return session.id


def _load(db: Session, session_id: uuid.UUID) -> tuple[UploadSession, UploadSlot]:
    upload_session = db.get(UploadSession, session_id)
    assert upload_session is not None
    slot = db.execute(sa.select(UploadSlot).where(UploadSlot.session_id == session_id)).scalar_one()
    return upload_session, slot


def test_re_put_cannot_replace_blob_after_commit_wins(
    pg_sessions: sessionmaker[Session],
    seeded: tuple[uuid.UUID, uuid.UUID],
    storage: LocalDiskStorage,
) -> None:
    session_id = _register_one(pg_sessions, seeded, storage)
    user_id, _ = seeded

    # First valid upload lands normally.
    with pg_sessions() as db:
        upload_session, slot = _load(db, session_id)
        receive_slot_blob(
            db,
            slot=slot,
            upload_session=upload_session,
            storage=storage,
            fileobj=io.BytesIO(b"v1"),
        )
        storage_key = slot.storage_key

    staged = threading.Event()
    staged_resume = threading.Event()
    gated = _GatedStorage(storage, staged=staged, staged_resume=staged_resume)
    errors: list[BaseException] = []

    def _re_put() -> None:
        with pg_sessions() as db:
            upload_session, slot = _load(db, session_id)
            try:
                receive_slot_blob(
                    db,
                    slot=slot,
                    upload_session=upload_session,
                    storage=gated,
                    fileobj=io.BytesIO(b"v2"),
                )
            except BaseException as exc:  # noqa: BLE001 - collected for assertions
                errors.append(exc)

    put_thread = threading.Thread(target=_re_put)
    put_thread.start()
    # The re-PUT has STAGED (pre-lock) — now let commit win the session row.
    assert staged.wait(_WAIT)
    with pg_sessions() as db:
        upload_session, _ = _load(db, session_id)
        user = db.get(User, user_id)
        assert user is not None
        docs = commit_session(db, user=user, upload_session=upload_session)
        assert len(docs) == 1
    staged_resume.set()
    put_thread.join(_WAIT)
    assert not put_thread.is_alive()

    # The re-PUT locked AFTER commit, saw COMMITTED under the lock, and rolled back its
    # staged object — the committed blob is still v1.
    assert len(errors) == 1
    assert isinstance(errors[0], UploadSessionNotOpen)
    assert errors[0].status == UploadSessionStatus.COMMITTED.value
    assert storage.get(storage_key) == b"v1"


def test_commit_serializes_after_in_flight_put_and_sees_received_slot(
    pg_sessions: sessionmaker[Session],
    seeded: tuple[uuid.UUID, uuid.UUID],
    storage: LocalDiskStorage,
) -> None:
    session_id = _register_one(pg_sessions, seeded, storage)
    user_id, _ = seeded

    gate = _PromoteGate()
    gated = _GatedStorage(storage, promote_gate=gate)
    put_errors: list[BaseException] = []
    commit_result: list[int] = []
    commit_errors: list[BaseException] = []

    def _put() -> None:
        with pg_sessions() as db:
            upload_session, slot = _load(db, session_id)
            try:
                receive_slot_blob(
                    db,
                    slot=slot,
                    upload_session=upload_session,
                    storage=gated,
                    fileobj=io.BytesIO(b"OK"),
                )
            except BaseException as exc:  # noqa: BLE001
                put_errors.append(exc)

    def _commit() -> None:
        with pg_sessions() as db:
            upload_session, _ = _load(db, session_id)
            user = db.get(User, user_id)
            assert user is not None
            try:
                commit_result.append(
                    len(commit_session(db, user=user, upload_session=upload_session))
                )
            except BaseException as exc:  # noqa: BLE001
                commit_errors.append(exc)

    put_thread = threading.Thread(target=_put)
    put_thread.start()
    # The PUT holds the session-row lock (it is inside promote, post-lock).
    assert gate.entered.wait(_WAIT)
    commit_thread = threading.Thread(target=_commit)
    commit_thread.start()
    # Release the PUT; commit must serialize AFTER it and then see received=True.
    gate.resume.set()
    put_thread.join(_WAIT)
    commit_thread.join(_WAIT)
    assert not put_thread.is_alive() and not commit_thread.is_alive()

    # Without the shared row lock the commit would have read received=False mid-PUT and
    # raised UploadIncomplete. With it, both succeed in serial order.
    assert put_errors == []
    assert commit_errors == []
    assert commit_result == [1]
    with pg_sessions() as db:
        upload_session, slot = _load(db, session_id)
        assert upload_session.status == UploadSessionStatus.COMMITTED.value
        assert slot.received is True
        assert storage.get(slot.storage_key) == b"OK"


def test_expiry_skips_row_locked_by_in_flight_put(
    pg_sessions: sessionmaker[Session],
    seeded: tuple[uuid.UUID, uuid.UUID],
    storage: LocalDiskStorage,
) -> None:
    # Session already past TTL, so it IS an expiry candidate while the PUT is in flight.
    session_id = _register_one(pg_sessions, seeded, storage, ttl_minutes=1)
    with pg_sessions() as db:
        upload_session = db.get(UploadSession, session_id)
        assert upload_session is not None
        upload_session.ttl_expires_at = _utcnow() - dt.timedelta(minutes=5)
        db.commit()

    gate = _PromoteGate()
    gated = _GatedStorage(storage, promote_gate=gate)
    put_errors: list[BaseException] = []

    def _put() -> None:
        with pg_sessions() as db:
            upload_session, slot = _load(db, session_id)
            try:
                receive_slot_blob(
                    db,
                    slot=slot,
                    upload_session=upload_session,
                    storage=gated,
                    fileobj=io.BytesIO(b"OK"),
                )
            except BaseException as exc:  # noqa: BLE001
                put_errors.append(exc)

    put_thread = threading.Thread(target=_put)
    put_thread.start()
    assert gate.entered.wait(_WAIT)  # the PUT holds the row lock now
    with pg_sessions() as db:
        expire_stale_sessions(db, storage=storage, now=_utcnow())
    # SKIP LOCKED: THIS session (row-locked by the in-flight PUT) was skipped, not
    # transitioned. Assert on the row — the sweep count is global across the shared dev DB.
    with pg_sessions() as db:
        row = db.get(UploadSession, session_id)
        assert row is not None
        assert row.status == UploadSessionStatus.OPEN.value
    gate.resume.set()
    put_thread.join(_WAIT)
    assert not put_thread.is_alive()
    assert put_errors == []
    with pg_sessions() as db:
        _, slot = _load(db, session_id)
        assert slot.received is True
        assert storage.get(slot.storage_key) == b"OK"  # nothing was deleted mid-PUT


def test_expiry_candidate_skipped_when_commit_changes_state_before_lock(
    pg_sessions: sessionmaker[Session],
    seeded: tuple[uuid.UUID, uuid.UUID],
    storage: LocalDiskStorage,
) -> None:
    session_id = _register_one(pg_sessions, seeded, storage, ttl_minutes=1)
    user_id, _ = seeded
    with pg_sessions() as db:
        upload_session, slot = _load(db, session_id)
        receive_slot_blob(
            db,
            slot=slot,
            upload_session=upload_session,
            storage=storage,
            fileobj=io.BytesIO(b"OK"),
        )
        upload_session.ttl_expires_at = _utcnow() - dt.timedelta(minutes=5)
        db.commit()
        storage_key = slot.storage_key

    scanned = threading.Event()
    resume = threading.Event()
    expiry_done = threading.Event()

    def _expire() -> None:
        def _barrier() -> None:
            scanned.set()
            assert resume.wait(_WAIT), "expiry barrier never released"

        with pg_sessions() as db:
            expire_stale_sessions(
                db, storage=storage, now=_utcnow(), on_candidates_scanned=_barrier
            )
        expiry_done.set()

    expiry_thread = threading.Thread(target=_expire)
    expiry_thread.start()
    assert scanned.wait(_WAIT)  # expiry has OBSERVED the candidate but not locked it
    with pg_sessions() as db:
        upload_session, _ = _load(db, session_id)
        user = db.get(User, user_id)
        assert user is not None
        commit_session(db, user=user, upload_session=upload_session)
    resume.set()
    expiry_thread.join(_WAIT)
    assert not expiry_thread.is_alive()
    assert expiry_done.is_set()

    # The under-lock recheck saw COMMITTED and skipped THIS session: it stays committed
    # (never flipped to expired) and its blob was not deleted. Assert on the row — the
    # sweep count is global across the shared dev DB.
    with pg_sessions() as db:
        upload_session, _ = _load(db, session_id)
        assert upload_session.status == UploadSessionStatus.COMMITTED.value
    assert storage.get(storage_key) == b"OK"
