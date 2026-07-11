"""Upload routes — register a batch, resume, PUT a slot's bytes, commit (04 §3 / corpus §4).

Thin by design (api_and_wire §4): validate, delegate to
:mod:`app.corpus.ingest.sessions`, return a view-model. Tenancy is by construction — reads
go through the firm-scoped session, so a cross-firm id 404s (never 403: an id must not leak
that a row exists in another tenant).

The storage backend is injected via :func:`get_object_storage` so tests override *that*
dependency with a tmp-dir :class:`~app.core.storage.LocalDiskStorage`. On the local backend
every slot's ``upload_url`` is the app-mediated ``PUT /api/uploads/slots/{slot_id}`` route
below (the dev "presign").
"""

from __future__ import annotations

import logging
import tempfile
import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.view_models import (
    DocumentView,
    UploadSessionView,
    UploadSlotView,
    document_to_view,
    upload_session_to_view,
    upload_slot_to_view,
)
from app.core.config import get_settings
from app.core.storage import ObjectStorage, get_storage
from app.corpus.ingest.sessions import (
    UploadIncomplete,
    UploadLimitExceeded,
    UploadSessionNotOpen,
    commit_session,
    receive_slot_blob,
    register_upload_session,
    upload_url_for,
)
from app.models.enums import UploadSessionStatus
from app.models.orm import Matter, UploadSession, UploadSlot, User
from app.models.schemas import UploadRegister

router = APIRouter(prefix="/api", tags=["uploads"])

# Upload-safety diagnostics (SEC-05/BUS-06). Non-PHI by construction: ids, byte counts, and
# booleans only — never filenames (client names can carry PHI) and never document content.
_LOG = logging.getLogger("clarionpi.uploads")


def get_object_storage() -> ObjectStorage:
    """Return the process-wide object storage. Tests override this with tmp-dir storage."""
    return get_storage()


# Module-level dependency singletons (FastAPI pattern; avoids a Depends() call in a default
# argument, which ruff B008 flags — the call is evaluated once here, not per signature).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)
_ObjectStorage = Depends(get_object_storage)


def _session_to_view(
    session: UploadSession, slots: list[UploadSlot], storage: ObjectStorage
) -> UploadSessionView:
    """Project a session + its slots, computing each slot's upload URL via the sessions layer."""
    slot_views = [upload_slot_to_view(slot, upload_url_for(slot, storage)) for slot in slots]
    return upload_session_to_view(session, slot_views)


@router.post(
    "/matters/{matter_id}/uploads",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
)
def register_uploads(
    matter_id: uuid.UUID,
    body: UploadRegister,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    storage: ObjectStorage = _ObjectStorage,
) -> UploadSessionView | JSONResponse:
    """Open an upload session for ``matter_id`` and return it with per-slot upload URLs.

    A matter outside the caller's firm scope is not found → ``404`` (never ``403``).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
        )
    try:
        upload_session = register_upload_session(
            session, user=user, matter=matter, files=body.files, storage=storage
        )
    except UploadLimitExceeded as exc:
        # Expected client error, never a 500: the typed 413 names which bound tripped.
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"error": "upload_limit_exceeded", "limit": exc.limit},
        )
    slots = list(
        session.scalars(
            select(UploadSlot)
            .where(UploadSlot.session_id == upload_session.id)
            .order_by(UploadSlot.ordinal)
        )
    )
    return _session_to_view(upload_session, slots, storage)


@router.get("/uploads/{session_id}", response_model=None)
def get_upload_session(
    session_id: uuid.UUID,
    session: Session = _TenantSession,
    storage: ObjectStorage = _ObjectStorage,
) -> UploadSessionView | JSONResponse:
    """Return an upload session and its slots (resume: client re-reads un-received slots).

    Out of firm scope → ``404`` (never ``403``).
    """
    upload_session = session.get(UploadSession, session_id)
    if upload_session is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "upload_session_not_found", "detail": f"no session {session_id}"},
        )
    slots = list(
        session.scalars(
            select(UploadSlot)
            .where(UploadSlot.session_id == upload_session.id)
            .order_by(UploadSlot.ordinal)
        )
    )
    return _session_to_view(upload_session, slots, storage)


@router.put("/uploads/slots/{slot_id}", response_model=None)
async def put_slot(
    slot_id: uuid.UUID,
    request: Request,
    session: Session = _TenantSession,
    storage: ObjectStorage = _ObjectStorage,
) -> UploadSlotView | JSONResponse:
    """Receive a slot's bytes (the app-mediated dev PUT). Overwrites on retry.

    The body is STREAMED into a bounded spool file while counting bytes — never read whole
    into memory (SEC-05). Crossing the configured per-file maximum stops consumption with a
    typed ``413``; any declared-size mismatch is a typed ``422``; on either rejection the
    slot's prior object and ``received`` state are untouched (storage is never invoked). A
    slot (or its session) outside firm scope → ``404``; a non-OPEN session → ``409``
    (pre-checked before the body is consumed, then re-checked under the row lock).
    """
    slot = session.get(UploadSlot, slot_id)
    if slot is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "upload_slot_not_found", "detail": f"no slot {slot_id}"},
        )
    upload_session = session.get(UploadSession, slot.session_id)
    if upload_session is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "upload_session_not_found",
                "detail": f"no session {slot.session_id}",
            },
        )
    # Reject a non-OPEN session BEFORE consuming the request body. The authoritative check
    # re-runs inside receive_slot_blob under the session-row lock.
    if upload_session.status != UploadSessionStatus.OPEN.value:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "upload_session_not_open", "status": upload_session.status},
        )

    max_file_bytes = get_settings().upload_max_bytes_per_file
    declared = slot.size_bytes
    received_bytes = 0
    with tempfile.NamedTemporaryFile() as spool:  # route staging; auto-removed on close
        async for chunk in request.stream():
            received_bytes += len(chunk)
            # The configured hard cap governs first (registration guarantees declared <=
            # max, so crossing the cap always also crosses the declared size).
            if received_bytes > max_file_bytes:
                _log_put(upload_session, slot, received_bytes, matched=False)
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"error": "upload_limit_exceeded", "limit": "max_file_bytes"},
                )
            if received_bytes > declared:
                _log_put(upload_session, slot, received_bytes, matched=False)
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"error": "upload_size_mismatch"},
                )
            spool.write(chunk)
        if received_bytes != declared:
            _log_put(upload_session, slot, received_bytes, matched=False)
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={"error": "upload_size_mismatch"},
            )
        _log_put(upload_session, slot, received_bytes, matched=True)
        spool.seek(0)
        try:
            receive_slot_blob(
                session,
                slot=slot,
                upload_session=upload_session,
                storage=storage,
                fileobj=spool,
            )
        except UploadSessionNotOpen as exc:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"error": "upload_session_not_open", "status": exc.status},
            )
    return upload_slot_to_view(slot, upload_url_for(slot, storage))


def _log_put(
    upload_session: UploadSession, slot: UploadSlot, actual_bytes: int, *, matched: bool
) -> None:
    """Debug diagnostic per slot PUT — ids and byte counts only, never filenames (PHI)."""
    _LOG.debug(
        "slot_put_received session_id=%s slot_id=%s declared_bytes=%d actual_bytes=%d "
        "size_matches=%s",
        upload_session.id,
        slot.id,
        slot.size_bytes,
        actual_bytes,
        matched,
    )


@router.post(
    "/uploads/{session_id}/commit",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
)
def commit_upload_session(
    session_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Commit a session: received slots become ``uploaded`` documents; return ``201``.

    Out of firm scope → ``404``; incomplete → ``409`` (``upload_incomplete`` + missing
    filenames); non-OPEN → ``409`` (``upload_session_not_open``).
    """
    upload_session = session.get(UploadSession, session_id)
    if upload_session is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "upload_session_not_found", "detail": f"no session {session_id}"},
        )
    try:
        documents = commit_session(session, user=user, upload_session=upload_session)
    except UploadIncomplete as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "upload_incomplete", "missing": exc.missing},
        )
    except UploadSessionNotOpen as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "upload_session_not_open", "status": exc.status},
        )
    views: list[DocumentView] = [document_to_view(doc) for doc in documents]
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "session_id": str(session_id),
            "documents": [v.model_dump(mode="json") for v in views],
        },
    )
