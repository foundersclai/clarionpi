"""Direct-ORM tests for the upload-sessions layer (app.corpus.ingest.sessions).

Exercises register/receive/commit/expire against the in-memory engine + tmp-dir storage,
proving: keys are minted from sanitized filenames; TTL is honored; a received blob
round-trips through storage; receiving into a committed session and committing twice both
refuse; commit fails loud (and creates no documents) when a slot never landed; a successful
commit creates ``uploaded``/``other``/``unique`` documents, back-links slots, and audits; the
TTL sweep flips only past-TTL OPEN sessions, deletes their received blobs, and blocks a later
commit; and pathological filenames still yield storage keys the local backend accepts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import LocalDiskStorage, StoredObjectNotFound
from app.corpus.ingest.sessions import (
    UploadIncomplete,
    UploadSessionNotOpen,
    _safe_name,
    _utcnow,
    commit_session,
    expire_stale_sessions,
    receive_slot_blob,
    register_upload_session,
)
from app.models.enums import DedupStatus, DocStatus, DocType, UploadSessionStatus
from app.models.orm import AuditEvent, CaseDocument, Matter, UploadSession, UploadSlot, User
from app.models.schemas import UploadFileDecl


def _decls(*names: str) -> list[UploadFileDecl]:
    return [UploadFileDecl(filename=name, size_bytes=len(name)) for name in names]


def _slots(db: Session, session: UploadSession) -> list[UploadSlot]:
    return list(
        db.scalars(
            select(UploadSlot)
            .where(UploadSlot.session_id == session.id)
            .order_by(UploadSlot.created_at, UploadSlot.id)
        )
    )


def _slot_named(db: Session, session: UploadSession, filename: str) -> UploadSlot:
    """Fetch a session's slot by filename.

    Slot order is deterministic-but-not-registration (see ``commit_session`` docstring), so
    tests that touch a specific file must address it by name, not by list position.
    """
    return db.scalars(
        select(UploadSlot).where(
            UploadSlot.session_id == session.id, UploadSlot.filename == filename
        )
    ).one()


def test_register_mints_sanitized_keys_and_fresh_ids(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    session = register_upload_session(
        db,
        user=dev_user,
        matter=matter,
        files=_decls("records.pdf", "bill 01.PDF"),
        storage=storage,
    )
    assert session.status == UploadSessionStatus.OPEN.value
    slots = _slots(db, session)
    assert {s.filename for s in slots} == {"records.pdf", "bill 01.PDF"}
    # Each key embeds matter, session, and the slot's own id, then the sanitized name.
    for slot in slots:
        prefix = f"matters/{matter.id}/uploads/{session.id}/{slot.id}/"
        assert slot.storage_key.startswith(prefix)
    assert _slot_named(db, session, "records.pdf").storage_key.endswith("/records.pdf")
    # Space -> underscore in the key, but the display filename is preserved verbatim.
    assert _slot_named(db, session, "bill 01.PDF").storage_key.endswith("/bill_01.PDF")
    # Ids are distinct per slot.
    assert len({s.id for s in slots}) == 2


def test_register_honors_explicit_ttl(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    before = _utcnow()
    session = register_upload_session(
        db,
        user=dev_user,
        matter=matter,
        files=_decls("a.pdf"),
        storage=storage,
        ttl_minutes=30,
    )
    after = _utcnow()
    # ttl_expires_at is ~30 minutes out (naive-UTC, compared naive-to-naive).
    assert before + timedelta(minutes=30) - timedelta(seconds=5) <= session.ttl_expires_at
    assert session.ttl_expires_at <= after + timedelta(minutes=30) + timedelta(seconds=5)


def test_receive_stores_blob_and_flags_received(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    session = register_upload_session(
        db, user=dev_user, matter=matter, files=_decls("a.pdf"), storage=storage
    )
    slot = _slots(db, session)[0]
    receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"PDF-BYTES")
    assert slot.received is True
    assert storage.get(slot.storage_key) == b"PDF-BYTES"


def test_receive_overwrites_on_retry(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    session = register_upload_session(
        db, user=dev_user, matter=matter, files=_decls("a.pdf"), storage=storage
    )
    slot = _slots(db, session)[0]
    receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"v1")
    # A re-PUT to an already-received slot in an OPEN session replaces the blob, no error.
    receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"v2")
    assert slot.received is True
    assert storage.get(slot.storage_key) == b"v2"


def test_receive_on_committed_session_raises(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    session = register_upload_session(
        db, user=dev_user, matter=matter, files=_decls("a.pdf"), storage=storage
    )
    slot = _slots(db, session)[0]
    receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"x")
    commit_session(db, user=dev_user, upload_session=session)
    with pytest.raises(UploadSessionNotOpen) as exc:
        receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"y")
    assert exc.value.status == UploadSessionStatus.COMMITTED.value


def test_commit_with_missing_slot_raises_and_creates_no_documents(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    session = register_upload_session(
        db,
        user=dev_user,
        matter=matter,
        files=_decls("got.pdf", "missing_a.pdf", "missing_b.pdf"),
        storage=storage,
    )
    got = _slot_named(db, session, "got.pdf")
    receive_slot_blob(db, slot=got, upload_session=session, storage=storage, data=b"x")
    with pytest.raises(UploadIncomplete) as exc:
        commit_session(db, user=dev_user, upload_session=session)
    # Exactly the un-received filenames (order is deterministic-but-not-registration; the
    # contract is the SET of missing names, so compare as a set).
    assert set(exc.value.missing) == {"missing_a.pdf", "missing_b.pdf"}
    assert "got.pdf" not in exc.value.missing
    # No documents created, session still OPEN, slots not back-linked.
    assert db.scalars(select(CaseDocument)).all() == []
    assert session.status == UploadSessionStatus.OPEN.value
    assert all(s.document_id is None for s in _slots(db, session))


def test_successful_commit_creates_uploaded_documents_and_audits(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    session = register_upload_session(
        db,
        user=dev_user,
        matter=matter,
        files=_decls("first.pdf", "second.pdf"),
        storage=storage,
    )
    slots = _slots(db, session)
    for slot in slots:
        receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"x")

    docs = commit_session(db, user=dev_user, upload_session=session)

    # One document per slot (order is deterministic-but-not-registration; assert the set).
    assert {d.filename for d in docs} == {"first.pdf", "second.pdf"}
    # docs and slots share the same (created_at, id) ordering, so they pair positionally.
    for doc, slot in zip(docs, slots, strict=True):
        assert doc.matter_id == matter.id
        assert doc.doc_type == DocType.OTHER.value
        assert doc.status == DocStatus.UPLOADED.value
        assert doc.dedup_status == DedupStatus.UNIQUE.value
        assert doc.source_label == slot.filename
        assert doc.storage_key == slot.storage_key
        assert doc.page_count == 0
    # Session committed and each slot back-links its document.
    assert session.status == UploadSessionStatus.COMMITTED.value
    linked = {s.document_id for s in _slots(db, session)}
    assert linked == {d.id for d in docs}
    # Audit trail: both register and commit events exist.
    kinds = set(db.scalars(select(AuditEvent.event_kind)).all())
    assert {"upload_session_registered", "upload_session_committed"} <= kinds


def test_commit_twice_raises_upload_session_not_open(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    session = register_upload_session(
        db, user=dev_user, matter=matter, files=_decls("a.pdf"), storage=storage
    )
    slot = _slots(db, session)[0]
    receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"x")
    commit_session(db, user=dev_user, upload_session=session)
    with pytest.raises(UploadSessionNotOpen) as exc:
        commit_session(db, user=dev_user, upload_session=session)
    assert exc.value.status == UploadSessionStatus.COMMITTED.value


def test_expire_sweep_flips_only_past_ttl_open_sessions(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    # A stale session with a received blob, and a fresh one that must survive.
    stale = register_upload_session(
        db, user=dev_user, matter=matter, files=_decls("old.pdf"), storage=storage
    )
    stale_slot = _slots(db, stale)[0]
    receive_slot_blob(db, slot=stale_slot, upload_session=stale, storage=storage, data=b"old")
    # Force the TTL into the past (naive UTC).
    stale.ttl_expires_at = _utcnow() - timedelta(minutes=1)
    db.commit()

    fresh = register_upload_session(
        db,
        user=dev_user,
        matter=matter,
        files=_decls("new.pdf"),
        storage=storage,
        ttl_minutes=60,
    )

    count = expire_stale_sessions(db, storage=storage, now=_utcnow())
    assert count == 1
    db.refresh(stale)
    db.refresh(fresh)
    assert stale.status == UploadSessionStatus.EXPIRED.value
    assert fresh.status == UploadSessionStatus.OPEN.value
    # The stale session's received blob was deleted.
    with pytest.raises(StoredObjectNotFound):
        storage.get(stale_slot.storage_key)
    # An expired session can no longer be committed.
    with pytest.raises(UploadSessionNotOpen):
        commit_session(db, user=dev_user, upload_session=stale)
    # The expired event was audited.
    kinds = db.scalars(select(AuditEvent.event_kind)).all()
    assert "upload_session_expired" in kinds


def test_expire_sweep_uses_naive_utc_boundary(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    # A tz-aware "now" passed by a caller must still compare correctly against the naive
    # column when converted to naive UTC by the caller; here we assert a not-yet-expired
    # session is untouched when now is just before its TTL.
    session = register_upload_session(
        db,
        user=dev_user,
        matter=matter,
        files=_decls("a.pdf"),
        storage=storage,
        ttl_minutes=10,
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    assert expire_stale_sessions(db, storage=storage, now=now) == 0
    db.refresh(session)
    assert session.status == UploadSessionStatus.OPEN.value


@pytest.mark.parametrize(
    ("filename", "expected_suffix"),
    [
        ("../../etc/passwd", ".._.._etc_passwd"),  # traversal neutralized: no ".." segment
        ("报告.pdf", "__.pdf"),  # each non-Latin glyph -> one underscore (2 glyphs -> "__")
    ],
)
def test_weird_filenames_produce_safe_keys(
    db: Session,
    dev_user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    filename: str,
    expected_suffix: str,
) -> None:
    session = register_upload_session(
        db,
        user=dev_user,
        matter=matter,
        files=[UploadFileDecl(filename=filename, size_bytes=1)],
        storage=storage,
    )
    slot = _slots(db, session)[0]
    assert slot.storage_key.endswith(f"/{expected_suffix}")
    # No traversal survives the sanitizer, so the resolved key stays under the storage root.
    assert "/../" not in slot.storage_key
    # The sanitized key is one the local backend accepts (round-trips through storage).
    receive_slot_blob(db, slot=slot, upload_session=session, storage=storage, data=b"x")
    assert storage.get(slot.storage_key) == b"x"


def test_safe_name_falls_back_to_file_when_nothing_survives() -> None:
    # UploadFileDecl enforces min_length=1, so the empty-name fallback is only reachable at
    # the sanitizer boundary — exercise it directly. An all-punctuation name that collapses to
    # a single "_" is representable; the empty string is the pure fallback.
    assert _safe_name("") == "file"
    assert _safe_name("report.pdf") == "report.pdf"
    assert _safe_name("A B/C") == "A_B_C"
