"""Upload sessions — resumable batch upload (component corpus_ingest §4 A2).

The client registers a batch (one slot per declared file), PUTs each file's bytes to the
slot's upload target, then *explicitly* commits: commit turns every received slot into a
:class:`~app.models.orm.CaseDocument` row in status ``uploaded`` (pre-classification). A
resume is just re-reading the session and its un-received slots. Abandoned sessions are
swept by TTL.

Upload-target contract: :func:`upload_url_for` hands out the storage backend's presigned PUT
URL when it can presign, and otherwise the app-mediated dev route
``/api/uploads/slots/{slot_id}`` (the local backend cannot presign — the app *is* the dev
presign). A re-PUT to an already-received slot in an OPEN session **overwrites** the blob:
retrying a flaky upload is legitimate, so the staged replacement swaps the object and no
error is raised. Receive/commit/expire all re-load the session row under ``FOR UPDATE`` and
recheck their lifecycle predicate under the lock (SEC-05), so a PUT cannot land after a
commit/expiry serialized first, and commit/expiry cannot transition mid-PUT.

Fail-loud discipline: commit refuses a session with any un-received slot
(:class:`UploadIncomplete` naming the missing filenames) — a firm that believes it uploaded
N files must discover a gap now, not at demand time. Receiving into a non-OPEN session is
refused (:class:`UploadSessionNotOpen`).

TTL sweep: :func:`expire_stale_sessions` runs on an UNscoped session (it is an ops sweep
across all firms) and is invoked directly by callers/tests — there is no scheduler at M1.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import IO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.storage import ObjectStorage
from app.core.tenancy import tenant_add
from app.models.enums import DedupStatus, DocStatus, DocType, UploadSessionStatus
from app.models.orm import CaseDocument, Matter, UploadSession, UploadSlot, User
from app.models.schemas import UploadFileDecl

# Storage-key path segment: keep the alnum/dot/dash/underscore glyphs, replace the rest.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _utcnow() -> datetime:
    """Naive-UTC now.

    The upload-session timestamp columns are ``DateTime(timezone=True)``, but the test DB is
    SQLite, which round-trips these as *naive*. We store naive UTC for every app-computed
    timestamp and compare naive-to-naive everywhere so SQLite and Postgres agree.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _as_naive_utc(value: datetime) -> datetime:
    """Normalize a loaded timestamp to naive UTC for Python-side comparison.

    SQLite round-trips ``DateTime(timezone=True)`` as naive; Postgres returns it tz-AWARE.
    Every app-computed timestamp is naive UTC, so Python-side comparisons must normalize
    the loaded side or they raise ``TypeError`` on Postgres.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _safe_name(filename: str) -> str:
    """Sanitize a client filename into a storage-key segment.

    Keeps ``[A-Za-z0-9._-]``, replaces every other character with ``_``, and falls back to
    ``"file"`` when nothing survives (empty name, all-punctuation, non-Latin script).
    """
    cleaned = _SAFE_NAME_RE.sub("_", filename)
    return cleaned or "file"


class UploadSessionNotOpen(Exception):
    """Raised when an operation requires an OPEN session but the session is not open.

    Carries the observed ``status`` so the caller/refusal can report the actual lifecycle
    state (committed, expired).
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"upload session is not open (status={status})")


class UploadIncomplete(Exception):
    """Raised when a commit is attempted with one or more slots not yet received.

    Carries ``missing`` — the filenames of the un-received slots — so the caller can tell the
    firm exactly which uploads never landed.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"upload session incomplete; missing: {missing}")


class UploadLimitExceeded(Exception):
    """Raised when an upload registration exceeds a configured limit (SEC-05).

    ``limit`` names the bound that tripped — ``max_files`` | ``max_file_bytes`` |
    ``max_session_bytes`` — and is the routing key for the typed ``413`` refusal.
    """

    def __init__(self, limit: str) -> None:
        self.limit = limit
        super().__init__(f"upload registration exceeds configured limit: {limit}")


def register_upload_session(
    db: Session,
    *,
    user: User,
    matter: Matter,
    files: list[UploadFileDecl],
    storage: ObjectStorage,
    ttl_minutes: int | None = None,
) -> UploadSession:
    """Open an upload session with one slot per declared file; return the session.

    Ids for the session and every slot are minted up front (``uuid.uuid4()``) so each slot's
    storage key can embed both ids deterministically:
    ``matters/{matter.id}/uploads/{session.id}/{slot.id}/{safe_name}``. ``ttl_minutes``
    defaults to ``settings.upload_session_ttl_minutes``. Writes an
    ``upload_session_registered`` audit event and commits.
    """
    settings = get_settings()
    # Enforce the configured registration limits BEFORE minting any slot id, storage key,
    # audit event, or row (SEC-05): a refused registration leaves zero persistent trace.
    if len(files) > settings.upload_max_files_per_session:
        raise UploadLimitExceeded("max_files")
    if any(decl.size_bytes > settings.upload_max_bytes_per_file for decl in files):
        raise UploadLimitExceeded("max_file_bytes")
    if sum(decl.size_bytes for decl in files) > settings.upload_max_bytes_per_session:
        raise UploadLimitExceeded("max_session_bytes")

    ttl = ttl_minutes if ttl_minutes is not None else settings.upload_session_ttl_minutes
    session_id = uuid.uuid4()

    session = UploadSession(
        id=session_id,
        matter_id=matter.id,
        status=UploadSessionStatus.OPEN.value,
        ttl_expires_at=_utcnow() + timedelta(minutes=ttl),
    )
    tenant_add(db, session, user.firm_id)

    for ordinal, decl in enumerate(files):
        slot_id = uuid.uuid4()
        safe_name = _safe_name(decl.filename)
        storage_key = f"matters/{matter.id}/uploads/{session_id}/{slot_id}/{safe_name}"
        slot = UploadSlot(
            id=slot_id,
            session_id=session_id,
            ordinal=ordinal,  # registration order — the client's stable pairing key (BUS-06)
            filename=decl.filename,
            size_bytes=decl.size_bytes,
            storage_key=storage_key,
            received=False,
        )
        tenant_add(db, slot, user.firm_id)

    record_event(
        db,
        firm_id=user.firm_id,
        actor_id=user.id,
        event_kind="upload_session_registered",
        payload={
            "session_id": str(session_id),
            "matter_id": str(matter.id),
            "file_count": len(files),
        },
    )
    db.commit()
    return session


def upload_url_for(slot: UploadSlot, storage: ObjectStorage) -> str:
    """Return the upload target for ``slot``: presigned PUT if the backend presigns, else the
    app-mediated dev route ``/api/uploads/slots/{slot.id}``.
    """
    presigned = storage.presign_put(slot.storage_key)
    if presigned is not None:
        return presigned
    return f"/api/uploads/slots/{slot.id}"


def _lock_session_row(db: Session, session_id: uuid.UUID) -> UploadSession | None:
    """Re-load ``UploadSession`` under ``FOR UPDATE``, refreshing stale identity-map state.

    ``populate_existing`` matters: a check against an already-loaded ORM object is stale and
    does not close the PUT-versus-commit/expiry race — the locked SELECT must overwrite the
    in-session attributes with the row that actually serialized. ``FOR UPDATE`` is a no-op on
    SQLite (single-writer anyway); the Postgres integration suite proves the serialization.
    """
    return db.execute(
        select(UploadSession)
        .where(UploadSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def receive_slot_blob(
    db: Session,
    *,
    slot: UploadSlot,
    upload_session: UploadSession,
    storage: ObjectStorage,
    fileobj: IO[bytes],
) -> UploadSlot:
    """Stage ``fileobj`` at the slot's key, lock + recheck the session, promote, commit.

    Refuses a non-OPEN session (:class:`UploadSessionNotOpen`) — checked cheaply on the
    caller's snapshot, then authoritatively on the row re-loaded under ``FOR UPDATE``
    immediately before promotion, so a commit/expiry that serialized first is honored. A
    re-PUT to an already-received slot in an OPEN session replaces the blob (idempotent
    retry of a flaky upload).

    The filesystem swap and the DB commit are NOT one atomic transaction; the staged-
    replacement protocol brackets them: promote → mark received → DB commit → finalize,
    with ``rollback()`` on any pre-commit failure restoring both the prior object and the
    prior received state.
    """
    if upload_session.status != UploadSessionStatus.OPEN.value:
        raise UploadSessionNotOpen(upload_session.status)
    staged = storage.stage_fileobj(slot.storage_key, fileobj)
    try:
        locked = _lock_session_row(db, upload_session.id)
        if locked is None or locked.status != UploadSessionStatus.OPEN.value:
            raise UploadSessionNotOpen(
                locked.status if locked is not None else UploadSessionStatus.EXPIRED.value
            )
        staged.promote()
        slot.received = True
        db.commit()
    except BaseException:
        staged.rollback()  # prior object (and received state, via db rollback) preserved
        db.rollback()
        raise
    staged.finalize()  # only after the DB commit succeeded
    return slot


def commit_session(db: Session, *, user: User, upload_session: UploadSession) -> list[CaseDocument]:
    """Commit an OPEN session: turn every received slot into an ``uploaded`` document.

    Refuses a non-OPEN session (:class:`UploadSessionNotOpen`) and a session with any
    un-received slot (:class:`UploadIncomplete`, listing the missing filenames) — no
    documents are created in that case. On success each slot becomes a
    :class:`~app.models.orm.CaseDocument` (doc_type ``other`` pre-classification, dedup
    ``unique``, status ``uploaded``) with ``slot.document_id`` back-linked; the session goes
    COMMITTED and an ``upload_session_committed`` audit event is written.

    Slots (and the returned documents) are ordered by ``ordinal`` — the client's
    registration order, the stable pairing contract (BUS-06). Documents are therefore
    created in exactly the order the client declared the files.

    OPEN-ness and completeness are rechecked on the session row re-loaded under
    ``FOR UPDATE`` (not the caller's possibly-stale snapshot), and the lock is held through
    document creation and the transaction commit — an in-flight slot PUT serializes either
    wholly before or wholly after this commit.
    """
    locked = _lock_session_row(db, upload_session.id)
    if locked is None:
        raise UploadSessionNotOpen(UploadSessionStatus.EXPIRED.value)
    if locked.status != UploadSessionStatus.OPEN.value:
        raise UploadSessionNotOpen(locked.status)

    # populate_existing: completeness must be evaluated from CURRENT row data under the
    # lock — a slot object already in this session's identity map (loaded before a
    # concurrent PUT committed) would otherwise report a stale received=False.
    slots = list(
        db.scalars(
            select(UploadSlot)
            .where(UploadSlot.session_id == upload_session.id)
            .order_by(UploadSlot.ordinal)
            .execution_options(populate_existing=True)
        )
    )
    missing = [slot.filename for slot in slots if not slot.received]
    if missing:
        raise UploadIncomplete(missing)

    documents: list[CaseDocument] = []
    for slot in slots:
        doc = CaseDocument(
            matter_id=upload_session.matter_id,
            doc_type=DocType.OTHER.value,  # pre-classification
            source_label=slot.filename,
            filename=slot.filename,
            storage_key=slot.storage_key,
            page_count=0,
            dedup_status=DedupStatus.UNIQUE.value,
            status=DocStatus.UPLOADED.value,
        )
        tenant_add(db, doc, user.firm_id)
        db.flush()  # assign doc.id before back-linking + the audit payload
        slot.document_id = doc.id
        documents.append(doc)

    locked.status = UploadSessionStatus.COMMITTED.value
    record_event(
        db,
        firm_id=user.firm_id,
        actor_id=user.id,
        event_kind="upload_session_committed",
        payload={
            "session_id": str(locked.id),
            "matter_id": str(locked.matter_id),
            "document_ids": [str(doc.id) for doc in documents],
        },
    )
    db.commit()
    return documents


def expire_stale_sessions(
    db: Session,
    *,
    storage: ObjectStorage,
    now: datetime,
    on_candidates_scanned: Callable[[], None] | None = None,
) -> int:
    """Expire every OPEN session past its TTL; return how many were expired.

    Runs on an UNscoped session — this is an ops sweep across all firms, not a per-request
    read. Each such session goes EXPIRED, every received slot's blob is deleted
    (``storage.delete`` is idempotent), and an ``upload_session_expired`` audit event
    (``actor_id`` None — no human actor) is written per session. There is no scheduler at M1;
    callers/tests invoke this directly with an explicit ``now``.

    Concurrency discipline: candidates come from a plain scan, but each row is re-locked
    (``FOR UPDATE SKIP LOCKED``) and its OPEN + past-TTL predicate RE-CHECKED under the lock
    before any blob delete or state change — a session that committed (or was already
    expired) between scan and lock is skipped, and a row currently locked by an active
    upload/commit is skipped for this sweep (the next sweep retries it). Each expiry commits
    per row, so locks are not held across unrelated sessions.

    ``on_candidates_scanned`` is a deterministic test barrier (fired once, after the scan and
    before any row lock); production callers leave it ``None``.
    """
    stale_ids = list(
        db.scalars(
            select(UploadSession.id).where(
                UploadSession.status == UploadSessionStatus.OPEN.value,
                UploadSession.ttl_expires_at < now,
            )
        )
    )
    if on_candidates_scanned is not None:
        on_candidates_scanned()
    expired = 0
    for session_id in stale_ids:
        locked = db.execute(
            select(UploadSession)
            .where(UploadSession.id == session_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if locked is None:
            continue  # locked by an active upload/commit — the next sweep retries
        still_expired = _as_naive_utc(locked.ttl_expires_at) < now
        if locked.status != UploadSessionStatus.OPEN.value or not still_expired:
            db.rollback()  # predicate no longer holds under the lock — release and skip
            continue
        slots = db.scalars(
            select(UploadSlot)
            .where(UploadSlot.session_id == locked.id)
            .execution_options(populate_existing=True)  # received flags read lock-fresh
        )
        for slot in slots:
            if slot.received:
                storage.delete(slot.storage_key)
        locked.status = UploadSessionStatus.EXPIRED.value
        record_event(
            db,
            firm_id=locked.firm_id,
            actor_id=None,
            event_kind="upload_session_expired",
            payload={
                "session_id": str(locked.id),
                "matter_id": str(locked.matter_id),
            },
        )
        db.commit()
        expired += 1
    return expired
