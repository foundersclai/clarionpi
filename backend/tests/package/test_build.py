"""ArtifactSet orchestration tests — build + store + record (M5 Wave B2).

Self-contained (own in-memory engine + firm/attorney/matter + local-disk storage, direct ORM),
matching ``tests/package/test_manifest.py``'s fixture style. Synthetic PDFs via
``tests/corpus/pdf_builders``; DraftSection rows hand-built (no drafter, no brain2 import). No PHI.

Coverage: happy path (four artifacts stored, storage.get round-trips non-empty, recorded sha256
matches the stored bytes), the ArtifactSet row is unique per (matter, draft_version,
registry_version), a second call returns reused=True (immutable), the artifact_set_built audit
event is written, and a blocked manifest propagates BinderBlocked with NO row and NO artifacts
persisted (atomicity).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.storage import LocalDiskStorage, StoredObjectNotFound
from app.models.enums import (
    ArtifactKind,
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    PhiDisposition,
    SectionValidation,
    UserRole,
)
from app.models.orm import (
    ArtifactSet,
    AuditEvent,
    CaseDocument,
    DemandDraft,
    DraftSection,
    Firm,
    Matter,
    User,
)
from app.models.schemas import ExhibitPickRequest
from app.package import binder as binder_mod
from app.package import build as build_mod
from app.package import manifest as mani
from tests.corpus.pdf_builders import build_text_pdf

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=2500,
        )
    )
    create_all_for_tests(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def storage() -> LocalDiskStorage:
    return LocalDiskStorage(tempfile.mkdtemp())


@pytest.fixture
def firm(db: Session) -> Firm:
    f = Firm(id=uuid.uuid4(), name="Test Firm")
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def attorney(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="atty@firm.test",
        display_name="Attorney",
        role=UserRole.ATTORNEY.value,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def matter(db: Session, firm: Firm) -> Matter:
    m_ = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Jane Doe",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.PACKAGE_ASSEMBLY.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m_)
    db.commit()
    return m_


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
        id=uuid.uuid4(),
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


def _cleared_pick(
    db: Session,
    matter: Matter,
    attorney: User,
    doc: CaseDocument,
    *,
    pages: list[int],
    sort_order: int,
) -> None:
    ex = mani.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=pages, sort_order=sort_order),
    )
    mani.set_phi_disposition(db, user=attorney, exhibit=ex, disposition=PhiDisposition.CLEARED)


def _passed_section(
    db: Session,
    matter: Matter,
    draft: DemandDraft,
    *,
    section_id: str,
    preview: str,
    sort_order: int,
) -> DraftSection:
    s = DraftSection(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        purpose="p",
        body_tokenized="x",
        rendered_preview=preview,
        registry_version=matter.registry_version,
        validation=SectionValidation.PASSED.value,
        spans=[],
        sort_order=sort_order,
    )
    db.add(s)
    db.commit()
    return s


def _draft(db: Session, matter: Matter) -> DemandDraft:
    d = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        strategy_plan_version=1,
        status="approved",
        memo="internal strategy memo",
    )
    db.add(d)
    db.flush()
    return d


def _happy_matter(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> DemandDraft:
    """A matter with one cleared exhibit + one passed section — a shippable package."""
    doc = _doc_with_pdf(db, storage, matter, filename="bill.pdf", page_texts=["p1", "p2"])
    _cleared_pick(db, matter, attorney, doc, pages=[1, 2], sort_order=1)
    draft = _draft(db, matter)
    _passed_section(
        db, matter, draft, section_id="liability", preview="The defendant is liable.", sort_order=1
    )
    return draft


# --------------------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------------------


def test_build_stores_four_artifacts_with_matching_sha(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    draft = _happy_matter(db, storage, matter, attorney)
    result = build_mod.build_artifact_set(
        db, storage, matter=matter, draft=draft, user=attorney, firm_name="Acme Law"
    )
    assert result.reused is False

    kinds = {a["kind"] for a in result.artifact_set.artifacts}
    assert kinds == {
        ArtifactKind.LETTER_DOCX.value,
        ArtifactKind.BINDER_PDF.value,
        ArtifactKind.CHRONOLOGY_XLSX.value,
        ArtifactKind.PROVENANCE_REPORT.value,
    }
    # Every artifact round-trips from storage non-empty and its recorded sha256 matches the bytes.
    for artifact in result.artifact_set.artifacts:
        blob = storage.get(artifact["object_key"])
        assert len(blob) > 0
        assert artifact["byte_count"] == len(blob)
        assert artifact["sha256"] == hashlib.sha256(blob).hexdigest()


def test_build_persists_row_with_versions_and_builder(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    draft = _happy_matter(db, storage, matter, attorney)
    build_mod.build_artifact_set(
        db, storage, matter=matter, draft=draft, user=attorney, firm_name="Acme Law"
    )
    rows = list(db.scalars(select(ArtifactSet).where(ArtifactSet.matter_id == matter.id)))
    assert len(rows) == 1
    row = rows[0]
    assert row.draft_version == draft.version
    assert row.registry_version == draft.registry_version
    assert row.draft_id == draft.id
    assert row.built_by == attorney.id
    assert row.firm_id == matter.firm_id


def test_build_writes_audit_event(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    draft = _happy_matter(db, storage, matter, attorney)
    build_mod.build_artifact_set(
        db, storage, matter=matter, draft=draft, user=attorney, firm_name="Acme Law"
    )
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.event_kind == "artifact_set_built"))
    )
    assert len(events) == 1
    payload = events[0].payload
    assert payload["draft_version"] == draft.version
    assert payload["registry_version"] == draft.registry_version
    assert set(payload["kinds"]) == {
        ArtifactKind.LETTER_DOCX.value,
        ArtifactKind.BINDER_PDF.value,
        ArtifactKind.CHRONOLOGY_XLSX.value,
        ArtifactKind.PROVENANCE_REPORT.value,
    }


def test_build_second_call_is_reused_and_no_duplicate_row(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    draft = _happy_matter(db, storage, matter, attorney)
    first = build_mod.build_artifact_set(
        db, storage, matter=matter, draft=draft, user=attorney, firm_name="Acme Law"
    )
    second = build_mod.build_artifact_set(
        db, storage, matter=matter, draft=draft, user=attorney, firm_name="Acme Law"
    )
    assert first.reused is False
    assert second.reused is True
    assert second.artifact_set.id == first.artifact_set.id
    # Exactly one row (immutable — the rebuild returned the existing set).
    rows = list(db.scalars(select(ArtifactSet).where(ArtifactSet.matter_id == matter.id)))
    assert len(rows) == 1
    # And exactly one audit event (the reuse path records nothing new).
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.event_kind == "artifact_set_built"))
    )
    assert len(events) == 1


# --------------------------------------------------------------------------------------
# Atomicity — a blocked manifest persists nothing
# --------------------------------------------------------------------------------------


def test_build_blocked_manifest_propagates_and_persists_nothing(
    db: Session, storage: LocalDiskStorage, matter: Matter, attorney: User
) -> None:
    doc = _doc_with_pdf(db, storage, matter, filename="bill.pdf", page_texts=["p1", "p2"])
    # Pick with includes but PHI left PENDING -> the manifest blocks the build.
    mani.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[1], sort_order=1),
    )
    draft = _draft(db, matter)
    _passed_section(db, matter, draft, section_id="liability", preview="Liable.", sort_order=1)

    with pytest.raises(binder_mod.BinderBlocked):
        build_mod.build_artifact_set(
            db, storage, matter=matter, draft=draft, user=attorney, firm_name="Acme Law"
        )

    # Atomicity: NO ArtifactSet row, NO artifact_set_built audit event, NO stored artifacts.
    assert db.scalar(select(ArtifactSet).where(ArtifactSet.matter_id == matter.id)) is None
    assert (
        db.scalar(select(AuditEvent).where(AuditEvent.event_kind == "artifact_set_built")) is None
    )
    key = build_mod._artifact_key(matter=matter, draft=draft, filename="letter.docx")
    with pytest.raises(StoredObjectNotFound):
        storage.get(key)
