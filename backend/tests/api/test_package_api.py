"""Package API tests (M5 Wave D1) — the build SSE stream, artifact list + download, cross-firm 404.

Mirrors ``test_analysis_api.py`` (conftest ``client`` + session-mode + seeded users) crossed with
``tests/package/test_build.py`` (synthetic PDFs via ``tests/corpus/pdf_builders``, exhibit picks +
PHI, a hand-built approved draft). ``get_object_storage`` is overridden with ONE shared tmp-dir
:class:`~app.core.storage.LocalDiskStorage` so the PDFs the seed writes are the bytes the build
route reads and the download route serves. Synthetic data only — no PHI.

Coverage:
- build fence: 409 off ``package_assembly``;
- build happy: 4 ``artifact_ready`` frames (with download urls) + ``gate_ready`` package_ready + the
  matter at ``package_ready`` + one ArtifactSet row;
- build blocked (pending PHI): a ``binder_blocked`` ERROR frame + the state UNCHANGED (no advance);
- artifacts list + a download bytes round-trip (content-type + Content-Disposition + sha matches the
  stored bytes) + a cross-firm download 404;
- rebuild: a second build returns ``reused=True`` (immutable) with no duplicate row.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import DEV_USER_EMAIL, DEV_USER_PASSWORD, seed_dev_users
from app.api.routes.uploads import get_object_storage
from app.core.config import get_settings
from app.core.storage import LocalDiskStorage
from app.main import app
from app.models.enums import (
    ArtifactKind,
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    PhiDisposition,
    SectionValidation,
)
from app.models.orm import ArtifactSet, CaseDocument, DemandDraft, DraftSection, Matter, User
from app.models.schemas import ExhibitPickRequest
from app.package import manifest as mani
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
    """One shared tmp-dir storage the route AND the seed helper both use (PDFs must round-trip)."""
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
            "client_display_name": "Package API Client",
            "claim_type": "mva",
            "incident_date": "2026-01-15",
            "jurisdiction": "AZ",
        },
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


def _sse_events(resp_text: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for frame in resp_text.split("\n\n"):
        frame = frame.strip()
        if not frame.startswith("event: "):
            continue
        lines = frame.split("\n")
        event = lines[0].removeprefix("event: ")
        parsed.append((event, json.loads(lines[1].removeprefix("data: "))))
    return parsed


# --------------------------------------------------------------------------------------
# Seed — a matter at package_assembly with an approved draft (mirrors tests/package/test_build.py)
# --------------------------------------------------------------------------------------


def _doc_with_pdf(
    db: Session,
    storage: LocalDiskStorage,
    matter: Matter,
    *,
    filename: str,
    page_texts: list[str],
) -> CaseDocument:
    key = f"blobs/{uuid.uuid4()}.pdf"
    doc = CaseDocument(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.BILL.value,
        source_label=filename,
        filename=filename,
        storage_key=key,
        page_count=len(page_texts),
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    db.commit()
    storage.put(key, build_text_pdf(page_texts))
    return doc


def _approved_draft(db: Session, matter: Matter) -> DemandDraft:
    draft = DemandDraft(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        strategy_plan_version=1,
        status="approved",
        memo="internal strategy memo",
    )
    db.add(draft)
    db.flush()
    db.add(
        DraftSection(
            firm_id=matter.firm_id,
            draft_id=draft.id,
            section_id="liability",
            purpose="p",
            body_tokenized="x",
            rendered_preview="The defendant is liable.",
            registry_version=matter.registry_version,
            validation=SectionValidation.PASSED.value,
            spans=[],
            sort_order=1,
        )
    )
    db.commit()
    return draft


def _seed_shippable(
    session_factory: sessionmaker[Session],
    storage: LocalDiskStorage,
    matter_id: uuid.UUID,
    *,
    clear_phi: bool,
) -> None:
    """One exhibit pick + a passed section; ``clear_phi`` decides shippable vs binder-blocked."""
    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        attorney = db.get(User, _dev_attorney_id())
        doc = _doc_with_pdf(db, storage, matter, filename="bill.pdf", page_texts=["p1", "p2"])
        ex = mani.upsert_exhibit_pick(
            db,
            user=attorney,
            matter=matter,
            pick=ExhibitPickRequest(document_id=doc.id, include_pages=[1, 2], sort_order=1),
        )
        if clear_phi:
            mani.set_phi_disposition(
                db, user=attorney, exhibit=ex, disposition=PhiDisposition.CLEARED
            )
        # Settle the exhibit tokens (the G2a-confirm side effect's job, BUS-05): the build
        # consumes ONLY settled tokens. Blocked entries (pending PHI) mint nothing — the
        # binder gate catches them first, exactly as before.
        mani.build_draft_manifest(db, matter=matter, mint_tokens=True)
        db.refresh(matter)
        _approved_draft(db, matter)
        matter.gate_state = GateState.PACKAGE_ASSEMBLY.value
        db.commit()
    finally:
        db.close()


def _dev_attorney_id() -> uuid.UUID:
    from app.api.deps import DEV_USER_ID

    return DEV_USER_ID


def _park(session_factory: sessionmaker[Session], matter_id: uuid.UUID, state: GateState) -> None:
    db = session_factory()
    try:
        db.get(Matter, matter_id).gate_state = state.value
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# build — fence + happy + blocked
# --------------------------------------------------------------------------------------


def test_build_fence_off_package_assembly(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.COMPLIANCE_REVIEW)  # not package_assembly

    resp = client.post(f"/api/matters/{matter_id}/package/build")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "gate_state_mismatch"


def test_build_happy_streams_and_advances(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _seed_shippable(seeded, storage, matter_id, clear_phi=True)

    resp = client.post(f"/api/matters/{matter_id}/package/build")
    assert resp.status_code == 200, resp.text
    events = _sse_events(resp.text)
    names = [n for n, _ in events]

    # 4 artifact_ready frames, each with a kind + a download url.
    artifact_frames = [d for n, d in events if n == "artifact_ready"]
    assert len(artifact_frames) == 4
    assert {d["artifact_kind"] for d in artifact_frames} == {
        ArtifactKind.LETTER_DOCX.value,
        ArtifactKind.BINDER_PDF.value,
        ArtifactKind.CHRONOLOGY_XLSX.value,
        ArtifactKind.PROVENANCE_REPORT.value,
    }
    for d in artifact_frames:
        assert d["url"].startswith(f"/api/matters/{matter_id}/artifacts/")
        assert d["url"].endswith(d["artifact_kind"])

    # gate_ready package_ready + a completed status.
    assert "gate_ready" in names
    gate_ready = [d for n, d in events if n == "gate_ready"][0]
    assert gate_ready["gate"] == "package_ready"
    completed = [d for n, d in events if n == "status" and d.get("state") == "completed"][0]
    assert completed["reused"] is False

    # Matter final; exactly one ArtifactSet row.
    db = seeded()
    try:
        assert db.get(Matter, matter_id).gate_state == GateState.PACKAGE_READY.value
        rows = list(db.scalars(select(ArtifactSet).where(ArtifactSet.matter_id == matter_id)))
        assert len(rows) == 1
    finally:
        db.close()


def test_build_blocked_pending_phi_errors_and_state_unchanged(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _seed_shippable(seeded, storage, matter_id, clear_phi=False)  # PHI pending -> binder blocked

    resp = client.post(f"/api/matters/{matter_id}/package/build")
    assert resp.status_code == 200, resp.text  # the stream opens; the failure is an ERROR frame
    events = _sse_events(resp.text)
    errors = [d for n, d in events if n == "error"]
    assert errors and errors[0]["error"] == "binder_blocked"
    assert errors[0]["reasons"]  # names why (pending PHI)
    # NO advance (a blocked package never ships); NO ArtifactSet row.
    db = seeded()
    try:
        assert db.get(Matter, matter_id).gate_state == GateState.PACKAGE_ASSEMBLY.value
        assert db.scalar(select(ArtifactSet).where(ArtifactSet.matter_id == matter_id)) is None
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# list + download (+ cross-firm 404) + rebuild reused
# --------------------------------------------------------------------------------------


def _build(client: TestClient, matter_id: uuid.UUID) -> None:
    resp = client.post(f"/api/matters/{matter_id}/package/build")
    assert resp.status_code == 200, resp.text


def test_artifacts_list_and_download_round_trip(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _seed_shippable(seeded, storage, matter_id, clear_phi=True)
    _build(client, matter_id)

    # List the sets.
    listing = client.get(f"/api/matters/{matter_id}/artifacts")
    assert listing.status_code == 200, listing.text
    sets = listing.json()["sets"]
    assert len(sets) == 1
    the_set = sets[0]
    assert the_set["draft_version"] == 1
    artifacts = the_set["artifacts"]
    assert {a["kind"] for a in artifacts} == {
        ArtifactKind.LETTER_DOCX.value,
        ArtifactKind.BINDER_PDF.value,
        ArtifactKind.CHRONOLOGY_XLSX.value,
        ArtifactKind.PROVENANCE_REPORT.value,
    }
    # No object_key leaks on the wire (only the kind-keyed url).
    assert all("object_key" not in a for a in artifacts)

    # Download the letter: content-type + Content-Disposition + sha matches the recorded digest.
    letter = next(a for a in artifacts if a["kind"] == ArtifactKind.LETTER_DOCX.value)
    dl = client.get(letter["url"])
    assert dl.status_code == 200, dl.text
    assert (
        dl.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert 'filename="letter.docx"' in dl.headers["content-disposition"]
    assert hashlib.sha256(dl.content).hexdigest() == letter["sha256"]

    # Download the binder (a PDF) too — a second media type path.
    binder = next(a for a in artifacts if a["kind"] == ArtifactKind.BINDER_PDF.value)
    dl_binder = client.get(binder["url"])
    assert dl_binder.status_code == 200
    assert dl_binder.headers["content-type"] == "application/pdf"
    assert hashlib.sha256(dl_binder.content).hexdigest() == binder["sha256"]


def test_download_cross_firm_404(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    storage: LocalDiskStorage,
    firm_b_matter_id: uuid.UUID,
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _seed_shippable(seeded, storage, matter_id, clear_phi=True)
    _build(client, matter_id)

    # The Firm-A caller's own set id, but addressed under the FIRM-B matter path -> 404 (the set is
    # not on that matter; existence must not leak cross-tenant).
    the_set = client.get(f"/api/matters/{matter_id}/artifacts").json()["sets"][0]
    resp = client.get(
        f"/api/matters/{firm_b_matter_id}/artifacts/{the_set['id']}/{ArtifactKind.LETTER_DOCX.value}"
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "matter_not_found"


def test_download_unknown_kind_404(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _seed_shippable(seeded, storage, matter_id, clear_phi=True)
    _build(client, matter_id)

    set_id = client.get(f"/api/matters/{matter_id}/artifacts").json()["sets"][0]["id"]
    resp = client.get(f"/api/matters/{matter_id}/artifacts/{set_id}/not_a_kind")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "artifact_not_found"


def test_rebuild_is_reused(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, storage: LocalDiskStorage
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _seed_shippable(seeded, storage, matter_id, clear_phi=True)
    _build(client, matter_id)

    # The first build advanced to package_ready; a rebuild is issued at package_assembly again
    # (the FE's re-issue path), and the immutable set is reused.
    _park(seeded, matter_id, GateState.PACKAGE_ASSEMBLY)
    resp = client.post(f"/api/matters/{matter_id}/package/build")
    assert resp.status_code == 200, resp.text
    events = _sse_events(resp.text)
    completed = [d for n, d in events if n == "status" and d.get("state") == "completed"][0]
    assert completed["reused"] is True

    # Still exactly one ArtifactSet row (immutable).
    db = seeded()
    try:
        rows = list(db.scalars(select(ArtifactSet).where(ArtifactSet.matter_id == matter_id)))
        assert len(rows) == 1
    finally:
        db.close()


def test_build_refused_typed_when_pack_unaudited_and_guard_on(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUS-02: with the audited-pack gate ON, the unaudited AZ stub refuses the build with a
    typed SSE error (no fingerprints/legal text on the wire), no advance, no side effects."""
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)  # pinned at creation to the unaudited stub
    _seed_shippable(seeded, storage, matter_id, clear_phi=True)

    monkeypatch.setenv("REQUIRE_AUDITED_RULE_PACK_FOR_PACKAGE", "true")
    get_settings.cache_clear()
    try:
        resp = client.post(f"/api/matters/{matter_id}/package/build")
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200, resp.text  # stream opens; refusal is a typed frame
    events = _sse_events(resp.text)
    errors = [d for n, d in events if n == "error"]
    assert errors and errors[0]["error"] == "rule_pack_unaudited"
    assert errors[0]["jurisdiction"] == "AZ"
    assert errors[0]["pack_version"] == "0.1.0"
    assert "fingerprint" not in json.dumps(errors)  # nothing sensitive on the wire

    db = seeded()
    try:
        assert db.get(Matter, matter_id).gate_state == GateState.PACKAGE_ASSEMBLY.value
        assert db.scalar(select(ArtifactSet).where(ArtifactSet.matter_id == matter_id)) is None
    finally:
        db.close()
