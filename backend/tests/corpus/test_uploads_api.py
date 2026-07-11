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

    # PUT each slot's bytes via its app-mediated upload_url (body length == declared).
    for slot in slots:
        put = client.put(slot["upload_url"], content=slot["filename"].encode())
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
        assert stored == slot["filename"].encode()
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
    put = client.put(first["upload_url"], content=b"first.pdf")  # body length == declared
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
    put = client.put(got["upload_url"], content=b"got.pdf")  # body length == declared
    assert put.status_code == 200

    commit = client.post(f"/api/uploads/{body['id']}/commit")
    assert commit.status_code == 409, commit.text
    payload = commit.json()
    assert payload["error"] == "upload_incomplete"
    assert payload["missing"] == ["missing.pdf"]


def test_put_size_mismatch_is_rejected_and_logged(
    client: TestClient, matter: Matter, caplog: pytest.LogCaptureFixture
) -> None:
    """SEC-05: a body larger than declared is a typed 422; the debug diagnostic (step 1
    evidence, retained at debug level) logs ids and byte counts only — never filenames."""
    body = _register(client, matter, "a.pdf")  # declared size 5 ("a.pdf")
    slot = body["slots"][0]
    with caplog.at_level("DEBUG", logger="clarionpi.uploads"):
        put = client.put(slot["upload_url"], content=b"only-three-matches-no")  # 21 bytes
    assert put.status_code == 422
    assert put.json() == {"error": "upload_size_mismatch"}
    diag = [r for r in caplog.records if "slot_put_received" in r.getMessage()]
    assert len(diag) == 1
    message = diag[0].getMessage()
    assert "declared_bytes=5" in message
    assert "size_matches=False" in message
    assert "a.pdf" not in message  # filenames never logged


def test_put_smaller_than_declared_is_rejected(client: TestClient, matter: Matter) -> None:
    body = _register(client, matter, "a.pdf")  # declared size 5
    slot = body["slots"][0]
    put = client.put(slot["upload_url"], content=b"abc")  # 3 < 5
    assert put.status_code == 422
    assert put.json() == {"error": "upload_size_mismatch"}


def test_put_exact_declared_size_stores_and_marks_received(
    client: TestClient, matter: Matter, storage: LocalDiskStorage, db: Session
) -> None:
    body = _register(client, matter, "a.pdf")  # declared size 5
    slot = body["slots"][0]
    put = client.put(slot["upload_url"], content=b"12345")
    assert put.status_code == 200
    assert put.json()["received"] is True
    key = db.scalars(select(UploadSlot).where(UploadSlot.id == uuid.UUID(slot["id"]))).one()
    assert storage.get(key.storage_key) == b"12345"


def test_put_over_configured_max_returns_413_without_storing(
    tiny_limits: None,
    client: TestClient,
    matter: Matter,
    storage: LocalDiskStorage,
    db: Session,
) -> None:
    """A body crossing the configured per-file cap stops with 413; nothing is stored."""
    body = _register(client, matter, "a.pdf")  # declared 5, cap 10 (tiny_limits)
    slot = body["slots"][0]
    put = client.put(slot["upload_url"], content=b"x" * 11)  # crosses the cap
    assert put.status_code == 413
    assert put.json() == {"error": "upload_limit_exceeded", "limit": "max_file_bytes"}
    row = db.scalars(select(UploadSlot).where(UploadSlot.id == uuid.UUID(slot["id"]))).one()
    assert row.received is False
    assert not storage.exists(row.storage_key)


def test_put_to_committed_session_is_409_before_body_is_consumed(
    client: TestClient, matter: Matter
) -> None:
    body = _register(client, matter, "a.pdf")
    slot = body["slots"][0]
    assert client.put(slot["upload_url"], content=b"12345").status_code == 200
    assert client.post(f"/api/uploads/{body['id']}/commit").status_code == 201
    # The route refuses on the pre-check (before any stream read); the service re-checks
    # under the row lock as the authoritative gate.
    put = client.put(slot["upload_url"], content=b"12345")
    assert put.status_code == 409
    assert put.json()["error"] == "upload_session_not_open"


def test_rejected_re_put_preserves_prior_object_and_received_state(
    client: TestClient, matter: Matter, storage: LocalDiskStorage, db: Session
) -> None:
    body = _register(client, matter, "a.pdf")  # declared 5
    slot = body["slots"][0]
    assert client.put(slot["upload_url"], content=b"GOOD1").status_code == 200
    # The retry has the wrong actual size → 422; the prior object and received survive.
    put = client.put(slot["upload_url"], content=b"bad")
    assert put.status_code == 422
    row = db.scalars(select(UploadSlot).where(UploadSlot.id == uuid.UUID(slot["id"]))).one()
    assert row.received is True
    assert storage.get(row.storage_key) == b"GOOD1"


@pytest.fixture
def tiny_limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Shrink the configured registration limits so the 413 refusals are testable."""
    from app.core.config import get_settings

    monkeypatch.setenv("UPLOAD_MAX_FILES_PER_SESSION", "2")
    monkeypatch.setenv("UPLOAD_MAX_BYTES_PER_FILE", "10")
    monkeypatch.setenv("UPLOAD_MAX_BYTES_PER_SESSION", "15")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _register_raw(client: TestClient, matter: Matter, files: list[dict]):  # noqa: ANN201
    return client.post(f"/api/matters/{matter.id}/uploads", json={"files": files})


def test_register_too_many_files_returns_413(
    tiny_limits: None, client: TestClient, matter: Matter, db: Session
) -> None:
    resp = _register_raw(
        client, matter, [{"filename": f"f{i}.pdf", "size_bytes": 1} for i in range(3)]
    )
    assert resp.status_code == 413, resp.text
    assert resp.json() == {"error": "upload_limit_exceeded", "limit": "max_files"}
    # Refused BEFORE minting anything: no session, no slots, no audit rows.
    assert db.scalars(select(UploadSession)).all() == []
    assert db.scalars(select(UploadSlot)).all() == []


def test_register_declared_file_over_max_returns_413(
    tiny_limits: None, client: TestClient, matter: Matter
) -> None:
    resp = _register_raw(client, matter, [{"filename": "big.pdf", "size_bytes": 11}])
    assert resp.status_code == 413
    assert resp.json() == {"error": "upload_limit_exceeded", "limit": "max_file_bytes"}


def test_register_aggregate_session_size_over_max_returns_413(
    tiny_limits: None, client: TestClient, matter: Matter
) -> None:
    resp = _register_raw(
        client,
        matter,
        [{"filename": "a.pdf", "size_bytes": 8}, {"filename": "b.pdf", "size_bytes": 8}],
    )
    assert resp.status_code == 413
    assert resp.json() == {"error": "upload_limit_exceeded", "limit": "max_session_bytes"}


def test_register_within_limits_still_works(
    tiny_limits: None, client: TestClient, matter: Matter
) -> None:
    resp = _register_raw(
        client,
        matter,
        [{"filename": "a.pdf", "size_bytes": 5}, {"filename": "b.pdf", "size_bytes": 5}],
    )
    assert resp.status_code == 201, resp.text


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
