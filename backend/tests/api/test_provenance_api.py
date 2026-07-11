"""Provenance API tests (M6 Wave A) — the document blob route + token→anchor provenance read.

Mirrors ``test_package_api.py``'s harness: the conftest ``client`` + per-test ``seeded`` users +
an in-test ``AUTH_MODE=session`` monkeypatch, and ONE shared tmp-dir
:class:`~app.core.storage.LocalDiskStorage` (via the ``get_object_storage`` override) so the PDF
bytes the seed writes are the exact bytes the blob route serves. Registry tokens are minted through
the real :mod:`app.engine.tokenizer.registry` (an attorney FACT with anchors, plus direct rows for
the unverified/disputed/amt outcomes). Synthetic data only — no PHI.

Coverage:
- blob: happy bytes round-trip (content-type + inline Content-Disposition + exact bytes vs
  storage); ``phi_access`` audited per fetch (two fetches → two rows); cross-firm 404; missing
  storage_key / missing stored object → ``blob_missing`` 404; filename header sanitized (``"`` +
  newline stripped).
- provenance: a minted FACT → 200 exact shape incl. server-enriched page_count/blob_url/bbox=null/
  superseded=false; a superseded anchor doc → superseded=true; an orphan id → ``token_not_found``
  404; malformed ids (``FACT_x`` / ``[[FACT_1]]`` / ``fact_1``) → ``invalid_token_id`` 422;
  unverified/disputed outcomes pass through; an AMT token's outcome comes from its stored status
  without live-hash checking; the response is wire-clean (no ``[[``); cross-firm 404.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    DEV_FIRM_ID,
    DEV_USER_EMAIL,
    DEV_USER_ID,
    DEV_USER_PASSWORD,
    seed_dev_users,
)
from app.api.routes.uploads import get_object_storage
from app.core.config import get_settings
from app.core.storage import LocalDiskStorage
from app.engine.tokenizer import registry
from app.main import app
from app.models.enums import (
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    TokenKind,
    TokenSource,
    TokenStatus,
)
from app.models.orm import (
    AuditEvent,
    CaseDocument,
    DedupDecision,
    FactToken,
    Matter,
    User,
)
from tests.corpus.pdf_builders import build_text_pdf

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    db = session_factory()
    try:
        seed_dev_users(db)
    finally:
        db.close()
    return session_factory


@pytest.fixture
def session_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTH_MODE", "session")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def storage() -> Iterator[LocalDiskStorage]:
    """One shared tmp-dir storage the blob route AND the seed helper both use (PDFs round-trip)."""
    s = LocalDiskStorage(tempfile.mkdtemp())
    app.dependency_overrides[get_object_storage] = lambda: s
    try:
        yield s
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": DEV_USER_PASSWORD})
    assert resp.status_code == 200, resp.text


def _create_matter(client: TestClient) -> uuid.UUID:
    resp = client.post(
        "/api/matters",
        json={
            "client_display_name": "Provenance API Client",
            "claim_type": "mva",
            "incident_date": "2026-01-15",
            "jurisdiction": "AZ",
            # WI-2: the four intake flags are REQUIRED; all-"no" is the in-box matter.
            "public_entity_involved": "no",
            "plaintiff_is_minor": "no",
            "wrongful_death": "no",
            "coverage_dispute": "no",
        },
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


def _audit_events(session_factory: sessionmaker[Session], *, kind: str) -> list[AuditEvent]:
    db = session_factory()
    try:
        return [e for e in db.execute(select(AuditEvent)).scalars() if e.event_kind == kind]
    finally:
        db.close()


def _add_document(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    filename: str = "bill.pdf",
    page_count: int = 3,
    page_texts: list[str] | None = None,
    storage: LocalDiskStorage | None = None,
    store_bytes: bool = True,
) -> tuple[uuid.UUID, bytes | None, str | None]:
    """Insert a CaseDocument (optionally storing PDF bytes); return (doc_id, pdf_bytes, key).

    ``store_bytes=False`` leaves ``storage_key`` NULL — the failed/expired-ingest case that yields
    ``blob_missing``.
    """
    texts = (
        page_texts if page_texts is not None else [f"page {i}" for i in range(1, page_count + 1)]
    )
    pdf_bytes = build_text_pdf(texts)
    key = f"blobs/{uuid.uuid4()}.pdf" if store_bytes else None
    db = session_factory()
    try:
        doc = CaseDocument(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            doc_type=DocType.BILL.value,
            source_label=filename,
            filename=filename,
            storage_key=key,
            page_count=len(texts),
            dedup_status=DedupStatus.UNIQUE.value,
            status=DocStatus.EXTRACTED.value,
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id
    finally:
        db.close()
    if store_bytes and storage is not None and key is not None:
        storage.put(key, pdf_bytes)
    return doc_id, (pdf_bytes if store_bytes else None), key


def _mint_fact(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    anchors: list[dict],
    display_form: str = "the incident",
) -> str:
    """Mint one attorney FACT with ``anchors`` via the registry; return its bare token id."""
    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        user = db.get(User, DEV_USER_ID)
        row = registry.mint_attorney_fact(
            db,
            matter=matter,
            user=user,
            display_form=display_form,
            value={"note": "synthetic"},
            anchors=anchors,
        )
        return row.token_id
    finally:
        db.close()


def _insert_token_row(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    kind: TokenKind,
    status_: TokenStatus,
    source: TokenSource,
    anchors: list[dict],
    display_form: str,
    value: object,
) -> str:
    """Insert a FactToken row directly (to exercise the unverified/disputed/AMT outcomes).

    Bumps the registry version so the row is the live latest for its slot, then writes it at that
    version. Returns the bare token id (``FACT_n`` / ``AMT_n``).
    """
    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        version = registry.bump_version(db, matter=matter, reason="test_seed")
        latest = registry._all_latest_rows(db, matter=matter)
        ordinal = registry._next_ordinal(latest)
        prefix = registry._KIND_PREFIX[kind]
        token_id = f"{prefix}_{ordinal}"
        row = FactToken(
            matter_id=matter.id,
            firm_id=matter.firm_id,
            token_id=token_id,
            registry_version=version,
            kind=kind.value,
            value=value,
            display_form=display_form,
            anchors=anchors,
            status=status_.value,
            source=source.value,
            source_ref=f"test:{uuid.uuid4()}",
        )
        db.add(row)
        db.commit()
        return token_id
    finally:
        db.close()


def _mark_superseded(
    session_factory: sessionmaker[Session], matter_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    """Record a SUPERSEDED dedup decision on ``document_id`` (the anchor-drop condition)."""
    db = session_factory()
    try:
        db.add(
            DedupDecision(
                firm_id=DEV_FIRM_ID,
                matter_id=matter_id,
                document_id=document_id,
                status=DedupStatus.DUPLICATE_OF.value,
                resolution=DedupResolution.SUPERSEDED.value,
            )
        )
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# Blob route
# --------------------------------------------------------------------------------------


def test_blob_happy_round_trips_bytes_and_audits(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    doc_id, pdf_bytes, _key = _add_document(
        seeded, matter_id, filename="records.pdf", page_texts=["a", "b", "c"], storage=storage
    )

    resp = client.get(f"/api/documents/{doc_id}/blob")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == 'inline; filename="records.pdf"'
    assert resp.content == pdf_bytes  # exact bytes vs what storage holds

    # A second fetch → a second phi_access row (the page-read is the audited event, per fetch).
    resp2 = client.get(f"/api/documents/{doc_id}/blob")
    assert resp2.status_code == 200
    events = _audit_events(seeded, kind="phi_access")
    assert len(events) == 2
    for e in events:
        assert e.payload["document_id"] == str(doc_id)
        assert e.payload["surface"] == "provenance_viewer"
        assert e.payload["filename"] == "records.pdf"
        assert e.actor_id == DEV_USER_ID
        assert e.firm_id == DEV_FIRM_ID


def test_blob_cross_firm_404(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    storage: LocalDiskStorage,
    firm_b_matter_id: uuid.UUID,
) -> None:
    _login(client, DEV_USER_EMAIL)  # Firm-A attorney
    # A doc that belongs to Firm B (created directly on that matter's firm) — a Firm-A caller must
    # 404 (existence must not leak), and NO phi_access row is written.
    db = seeded()
    try:
        firm_b_id = db.get(Matter, firm_b_matter_id).firm_id
        doc = CaseDocument(
            firm_id=firm_b_id,
            matter_id=firm_b_matter_id,
            doc_type=DocType.BILL.value,
            source_label="b.pdf",
            filename="b.pdf",
            storage_key="blobs/b.pdf",
            page_count=1,
            dedup_status=DedupStatus.UNIQUE.value,
            status=DocStatus.EXTRACTED.value,
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id
    finally:
        db.close()
    storage.put("blobs/b.pdf", build_text_pdf(["x"]))

    resp = client.get(f"/api/documents/{doc_id}/blob")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "document_not_found"
    assert _audit_events(seeded, kind="phi_access") == []


def test_blob_missing_storage_key_404(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    doc_id, _b, _k = _add_document(seeded, matter_id, store_bytes=False)  # storage_key stays NULL

    resp = client.get(f"/api/documents/{doc_id}/blob")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "blob_missing"
    # A blob that never served bytes leaves no phi_access row.
    assert _audit_events(seeded, kind="phi_access") == []


def test_blob_missing_stored_object_404(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    # A storage_key is set but no object was ever put there → StoredObjectNotFound → blob_missing.
    db = seeded()
    try:
        doc = CaseDocument(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            doc_type=DocType.BILL.value,
            source_label="ghost.pdf",
            filename="ghost.pdf",
            storage_key="blobs/never-stored.pdf",
            page_count=1,
            dedup_status=DedupStatus.UNIQUE.value,
            status=DocStatus.EXTRACTED.value,
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id
    finally:
        db.close()

    resp = client.get(f"/api/documents/{doc_id}/blob")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "blob_missing"


def test_blob_filename_header_sanitized(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    # A hostile filename with a quote + newline; the served header must carry neither.
    nasty = 'a"b\ninjected: x.pdf'
    doc_id, _b, _k = _add_document(seeded, matter_id, filename=nasty, storage=storage)

    resp = client.get(f"/api/documents/{doc_id}/blob")
    assert resp.status_code == 200, resp.text
    disp = resp.headers["content-disposition"]
    assert '"' not in disp.removeprefix('inline; filename="').removesuffix('"')
    assert "\n" not in disp and "\r" not in disp
    # The quote + newline are dropped (not replaced): 'a"b\ninjected: x.pdf' -> 'abinjected: x.pdf'.
    assert disp == 'inline; filename="abinjected: x.pdf"'


# --------------------------------------------------------------------------------------
# Provenance endpoint
# --------------------------------------------------------------------------------------


def test_provenance_minted_fact_exact_shape(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    doc_id, _b, _k = _add_document(seeded, matter_id, page_count=12, store_bytes=False)
    token_id = _mint_fact(
        seeded,
        matter_id,
        anchors=[{"document_id": str(doc_id), "page": 3}],
        display_form="the fall",
    )

    resp = client.get(f"/api/matters/{matter_id}/provenance/{token_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "token_id": token_id,
        "display_form": "the fall",
        "outcome": "ok",
        "source": "attorney",
        "anchors": [
            {
                "document_id": str(doc_id),
                "page": 3,
                "bbox": None,
                "blob_url": f"/api/documents/{doc_id}/blob",
                "page_count": 12,
                "superseded": False,
            }
        ],
    }
    # The endpoint does NOT audit (only the blob fetch does).
    assert _audit_events(seeded, kind="phi_access") == []


def test_provenance_superseded_anchor_flags_true(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    doc_id, _b, _k = _add_document(seeded, matter_id, page_count=5, store_bytes=False)
    token_id = _mint_fact(seeded, matter_id, anchors=[{"document_id": str(doc_id), "page": 2}])
    _mark_superseded(seeded, matter_id, doc_id)

    resp = client.get(f"/api/matters/{matter_id}/provenance/{token_id}")
    assert resp.status_code == 200, resp.text
    anchor = resp.json()["anchors"][0]
    assert anchor["superseded"] is True
    assert anchor["page_count"] == 5
    assert anchor["blob_url"] == f"/api/documents/{doc_id}/blob"


def test_provenance_orphan_id_404(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)

    resp = client.get(f"/api/matters/{matter_id}/provenance/FACT_999")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "token_not_found"


@pytest.mark.parametrize("bad_id", ["FACT_x", "[[FACT_1]]", "fact_1", "FACT", "FACT_", "XYZ_1"])
def test_provenance_malformed_id_422(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, bad_id: str
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)

    resp = client.get(f"/api/matters/{matter_id}/provenance/{bad_id}")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_token_id"


def test_provenance_unverified_and_disputed_pass_through(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    doc_id, _b, _k = _add_document(seeded, matter_id, page_count=4, store_bytes=False)

    unverified_id = _insert_token_row(
        seeded,
        matter_id,
        kind=TokenKind.FACT,
        status_=TokenStatus.UNVERIFIED,
        source=TokenSource.EXTRACTOR,
        anchors=[{"document_id": str(doc_id), "page": 1}],
        display_form="an unverified fact",
        value={"k": "v"},
    )
    disputed_id = _insert_token_row(
        seeded,
        matter_id,
        kind=TokenKind.FACT,
        status_=TokenStatus.DISPUTED,
        source=TokenSource.ATTORNEY,
        anchors=[{"document_id": str(doc_id), "page": 2}],
        display_form="a disputed fact",
        value={"k": "v"},
    )

    unv = client.get(f"/api/matters/{matter_id}/provenance/{unverified_id}")
    assert unv.status_code == 200, unv.text
    assert unv.json()["outcome"] == "unverified"
    assert unv.json()["source"] == "extractor"
    assert unv.json()["anchors"][0]["page"] == 1

    dis = client.get(f"/api/matters/{matter_id}/provenance/{disputed_id}")
    assert dis.status_code == 200, dis.text
    assert dis.json()["outcome"] == "disputed"
    assert dis.json()["source"] == "attorney"


def test_provenance_amt_outcome_without_live_hash_check(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    # An AMT whose stored ledger_hash would NOT match a live re-hash still resolves ``ok`` here —
    # the provenance endpoint passes NO live_ledger_hash, so it never re-hashes (it shows
    # provenance, not the G3 amount-drift verdict). A VERIFIED AMT therefore reports outcome ``ok``.
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    amt_id = _insert_token_row(
        seeded,
        matter_id,
        kind=TokenKind.AMOUNT,
        status_=TokenStatus.VERIFIED,
        source=TokenSource.EXTRACTOR,
        anchors=[],
        display_form="$1,500.00",
        value={"cents": 150000},
    )
    # Give it a stored ledger_hash that a live recompute would never reproduce — proof the endpoint
    # does not re-verify it (else the outcome would be amt_mismatch).
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        row = registry._latest(db, matter=matter, token=f"[[{amt_id}]]")
        row.ledger_ref = {"line_ids": ["x"], "column": "billed"}
        row.ledger_hash = "definitely-not-the-live-hash"
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/api/matters/{matter_id}/provenance/{amt_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ok"  # NOT amt_mismatch — no live-hash check on this surface
    assert body["anchors"] == []


def test_provenance_response_survives_wire_guard(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    doc_id, _b, _k = _add_document(seeded, matter_id, page_count=2, store_bytes=False)
    token_id = _mint_fact(seeded, matter_id, anchors=[{"document_id": str(doc_id), "page": 1}])

    resp = client.get(f"/api/matters/{matter_id}/provenance/{token_id}")
    assert resp.status_code == 200, resp.text
    # The bare id is on the wire; NOTHING token-shaped ([[..]]) is.
    assert token_id in resp.text
    assert "[[" not in resp.text


def test_provenance_cross_firm_404(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    firm_b_matter_id: uuid.UUID,
) -> None:
    _login(client, DEV_USER_EMAIL)  # Firm-A attorney

    resp = client.get(f"/api/matters/{firm_b_matter_id}/provenance/FACT_1")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "matter_not_found"
