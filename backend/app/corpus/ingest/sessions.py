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
retrying a flaky upload is legitimate, so ``storage.put`` simply replaces the object and no
error is raised.

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
from datetime import UTC, datetime, timedelta

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
    ttl = ttl_minutes if ttl_minutes is not None else get_settings().upload_session_ttl_minutes
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


def receive_slot_blob(
    db: Session,
    *,
    slot: UploadSlot,
    upload_session: UploadSession,
    storage: ObjectStorage,
    data: bytes,
) -> UploadSlot:
    """Store ``data`` at the slot's key and mark it received; return the slot.

    Refuses a non-OPEN session (:class:`UploadSessionNotOpen`). A re-PUT to an
    already-received slot overwrites the blob (idempotent retry of a flaky upload).
    """
    if upload_session.status != UploadSessionStatus.OPEN.value:
        raise UploadSessionNotOpen(upload_session.status)
    # Declared size is advisory at M1: a mismatch against len(data) is not enforced here.
    storage.put(slot.storage_key, data)
    slot.received = True
    db.commit()
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
    """
    if upload_session.status != UploadSessionStatus.OPEN.value:
        raise UploadSessionNotOpen(upload_session.status)

    slots = list(
        db.scalars(
            select(UploadSlot)
            .where(UploadSlot.session_id == upload_session.id)
            .order_by(UploadSlot.ordinal)
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

    upload_session.status = UploadSessionStatus.COMMITTED.value
    record_event(
        db,
        firm_id=user.firm_id,
        actor_id=user.id,
        event_kind="upload_session_committed",
        payload={
            "session_id": str(upload_session.id),
            "matter_id": str(upload_session.matter_id),
            "document_ids": [str(doc.id) for doc in documents],
        },
    )
    db.commit()
    return documents


def expire_stale_sessions(db: Session, *, storage: ObjectStorage, now: datetime) -> int:
    """Expire every OPEN session past its TTL; return how many were expired.

    Runs on an UNscoped session — this is an ops sweep across all firms, not a per-request
    read. Each such session goes EXPIRED, every received slot's blob is deleted
    (``storage.delete`` is idempotent), and an ``upload_session_expired`` audit event
    (``actor_id`` None — no human actor) is written per session. There is no scheduler at M1;
    callers/tests invoke this directly with an explicit ``now``.
    """
    stale = list(
        db.scalars(
            select(UploadSession).where(
                UploadSession.status == UploadSessionStatus.OPEN.value,
                UploadSession.ttl_expires_at < now,
            )
        )
    )
    for session in stale:
        slots = db.scalars(select(UploadSlot).where(UploadSlot.session_id == session.id))
        for slot in slots:
            if slot.received:
                storage.delete(slot.storage_key)
        session.status = UploadSessionStatus.EXPIRED.value
        record_event(
            db,
            firm_id=session.firm_id,
            actor_id=None,
            event_kind="upload_session_expired",
            payload={
                "session_id": str(session.id),
                "matter_id": str(session.matter_id),
            },
        )
    db.commit()
    return len(stale)
