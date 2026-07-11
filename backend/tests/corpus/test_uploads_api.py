"""Route tests for the uploads API (app.api.routes.uploads).

Drives the router through a local FastAPI app (``make_client``) with ``get_object_storage``
overridden to the tmp-dir ``storage`` fixture, so the app and direct-ORM assertions share one
disk. Covers: the full register -> PUT-per-slot -> commit happy path; a resume GET reflecting
received flags; the local-backend upload_url shape; a commit-incomplete ``409`` body; and
cross-firm isolation (a Firm-B slot's PUT and a Firm-B session GET both ``404``, never leaking
existence).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.uploads import get_object_storage, router
from app.core.storage import LocalDiskStorage
from app.models.enums import DocStatus, DocType, UploadSessionStatus
from app.models.orm import Matter, UploadSession, UploadSlot

from .conftest import FIRM_B_ID


@pytest.fixture
def client(
    make_client: Callable[[APIRouter], TestClient], storage: LocalDiskStorage
) -> Iterator[TestClient]:
    """A TestClient for the uploads router with storage pinned to the tmp-dir fixture."""
    c = make_client(router)
    c.app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        yield c
    finally:
        c.app.dependency_overrides.clear()


def _register(client: TestClient, matter: Matter, *names: str) -> dict:
    resp = client.post(
        f"/api/matters/{matter.id}/uploads",
        json={"files": [{"filename": n, "size_bytes": len(n)} for n in names]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_register_put_commit_happy_flow(
    client: TestClient, matter: Matter, storage: LocalDiskStorage, db: Session
) -> None:
    body = _register(client, matter, "records.pdf", "bill.pdf")
    assert body["status"] == UploadSessionStatus.OPEN.value
    assert body["matter_id"] == str(matter.id)
    slots = body["slots"]
    # Slots come back in ordinal (= registration) order with 0..n-1 ordinals (BUS-06).
    assert [(s["ordinal"], s["filename"]) for s in slots] == [
        (0, "records.pdf"),
        (1, "bill.pdf"),
    ]
    assert all(s["received"] is False for s in slots)

    # PUT each slot's bytes via its app-mediated upload_url.
    for slot in slots:
        put = client.put(slot["upload_url"], content=f"BYTES-{slot['filename']}".encode())
        assert put.status_code == 200, put.text
        assert put.json()["received"] is True

    commit = client.post(f"/api/uploads/{body['id']}/commit")
    assert commit.status_code == 201, commit.text
    payload = commit.json()
    assert payload["session_id"] == body["id"]
    docs = payload["documents"]
    assert {d["filename"] for d in docs} == {"records.pdf", "bill.pdf"}
    for d in docs:
        assert d["status"] == DocStatus.UPLOADED.value
        assert d["doc_type"] == DocType.OTHER.value
    # The blobs really landed in the shared storage.
    for slot in slots:
        stored = storage.get(
            db.scalars(select(UploadSlot).where(UploadSlot.id == uuid.UUID(slot["id"])))
            .one()
            .storage_key
        )
        assert stored == f"BYTES-{slot['filename']}".encode()
    # Session flipped to committed.
    session_row = db.scalars(
        select(UploadSession).where(UploadSession.id == uuid.UUID(body["id"]))
    ).one()
    assert session_row.status == UploadSessionStatus.COMMITTED.value


def test_register_upload_url_is_app_route_on_local_backend(
    client: TestClient, matter: Matter
) -> None:
    body = _register(client, matter, "a.pdf")
    slot = body["slots"][0]
    # LocalDiskStorage cannot presign, so the app hands out its own PUT route.
    assert slot["upload_url"] == f"/api/uploads/slots/{slot['id']}"


def test_resume_get_reflects_received_flags(client: TestClient, matter: Matter) -> None:
    body = _register(client, matter, "first.pdf", "second.pdf")
    first, second = body["slots"]
    put = client.put(first["upload_url"], content=b"x")
    assert put.status_code == 200

    resumed = client.get(f"/api/uploads/{body['id']}")
    assert resumed.status_code == 200
    resumed_slots = resumed.json()["slots"]
    # Resume returns slots ordered by ordinal (registration order).
    assert [s["ordinal"] for s in resumed_slots] == [0, 1]
    got = {s["id"]: s["received"] for s in resumed_slots}
    assert got[first["id"]] is True
    assert got[second["id"]] is False


def test_commit_incomplete_returns_409(client: TestClient, matter: Matter) -> None:
    body = _register(client, matter, "got.pdf", "missing.pdf")
    # Slot order is not registration order, so address "got.pdf" by name, not by position.
    got = next(s for s in body["slots"] if s["filename"] == "got.pdf")
    client.put(got["upload_url"], content=b"x")

    commit = client.post(f"/api/uploads/{body['id']}/commit")
    assert commit.status_code == 409, commit.text
    payload = commit.json()
    assert payload["error"] == "upload_incomplete"
    assert payload["missing"] == ["missing.pdf"]


def test_put_size_mismatch_is_logged_but_currently_accepted(
    client: TestClient, matter: Matter, caplog: pytest.LogCaptureFixture
) -> None:
    """Diagnostic (SEC-05/BUS-06 step 1): declared vs actual byte counts are logged.

    Today a mismatched body is silently accepted — this test records that evidence (the
    ``size_matches=False`` log plus the 200) before the enforcement lands. Only ids and byte
    counts are logged, never filenames.
    """
    body = _register(client, matter, "a.pdf")  # declared size 5 ("a.pdf")
    slot = body["slots"][0]
    with caplog.at_level("DEBUG", logger="clarionpi.uploads"):
        put = client.put(slot["upload_url"], content=b"only-three-matches-no")  # 21 bytes
    assert put.status_code == 200  # current behavior: no enforcement yet
    diag = [r for r in caplog.records if "slot_put_received" in r.getMessage()]
    assert len(diag) == 1
    message = diag[0].getMessage()
    assert "declared_bytes=5" in message
    assert "actual_bytes=21" in message
    assert "size_matches=False" in message
    assert "a.pdf" not in message  # filenames never logged


def test_register_unknown_matter_returns_404(client: TestClient) -> None:
    resp = client.post(
        f"/api/matters/{uuid.uuid4()}/uploads",
        json={"files": [{"filename": "a.pdf", "size_bytes": 1}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "matter_not_found"


def _seed_firm_b_session(db: Session, firm_b_matter: Matter) -> tuple[UploadSession, UploadSlot]:
    """Create a Firm-B upload session + one un-received slot directly (bypassing the API)."""
    session = UploadSession(
        firm_id=FIRM_B_ID,
        matter_id=firm_b_matter.id,
        status=UploadSessionStatus.OPEN.value,
        ttl_expires_at=dt.datetime(2099, 1, 1),
    )
    db.add(session)
    db.flush()
    slot = UploadSlot(
        firm_id=FIRM_B_ID,
        session_id=session.id,
        ordinal=0,
        filename="b.pdf",
        size_bytes=1,
        storage_key=f"matters/{firm_b_matter.id}/uploads/{session.id}/x/b.pdf",
        received=False,
    )
    db.add(slot)
    db.commit()
    return session, slot


def test_put_firm_b_slot_returns_404(
    client: TestClient, db: Session, firm_b_matter: Matter
) -> None:
    _session, slot = _seed_firm_b_session(db, firm_b_matter)
    # The Firm-A caller cannot see (or write) a Firm-B slot — 404, not 403.
    resp = client.put(f"/api/uploads/slots/{slot.id}", content=b"x")
    assert resp.status_code == 404
    assert resp.json()["error"] == "upload_slot_not_found"


def test_get_firm_b_session_returns_404(
    client: TestClient, db: Session, firm_b_matter: Matter
) -> None:
    session, _slot = _seed_firm_b_session(db, firm_b_matter)
    resp = client.get(f"/api/uploads/{session.id}")
    assert resp.status_code == 404
    assert resp.json()["error"] == "upload_session_not_found"
