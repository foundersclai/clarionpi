"""Draft binder manifest + exhibit picks + EX minting tests (M4 Wave B2).

Self-contained (own in-memory engine + firm/attorney/paralegal/matter, direct ORM), matching
``tests/engine/test_registry_amounts.py``'s fixture style. Synthetic data only — no PHI.

Coverage: pick upsert create/update, the three typed pick refusals (page-range, include∩exclude,
cross-matter document), collation ordering, every integrity verdict (empty include, out-of-range
after a page_count shrink, doc superseded), the blocking list (pending PHI blocks only entries WITH
includes), attorney-only PHI disposition, and the EX-mint pass (idempotent, stable ids, display
forms, one version bump, token stamped) plus ``mint_exhibits`` unit behavior (idempotency, EXHIBIT
kind, source_ref shape, shared-ordinal interleaving with FACT/AMT).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.tenancy import tenant_add
from app.engine.tokenizer import registry
from app.models.enums import (
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    FlagKind,
    FlagSeverity,
    GateState,
    PhiDisposition,
    TokenKind,
    TokenSource,
    TokenStatus,
    UserRole,
)
from app.models.orm import (
    CaseDocument,
    DedupDecision,
    Exhibit,
    FactToken,
    Firm,
    Matter,
    MedicalEncounter,
    RegistryVersion,
    RiskFlag,
    User,
)
from app.models.schemas import AmountFact, ExhibitPickRequest
from app.package import manifest as m

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
def paralegal(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="para@firm.test",
        display_name="Paralegal",
        role=UserRole.PARALEGAL.value,
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
        gate_state=GateState.EVIDENCE_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m_)
    db.commit()
    return m_


def _add_document(
    db: Session, matter: Matter, *, filename: str = "bill.pdf", page_count: int = 5
) -> CaseDocument:
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.BILL.value,
        source_label=filename,
        filename=filename,
        page_count=page_count,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    db.commit()
    return doc


# --------------------------------------------------------------------------------------
# upsert_exhibit_pick — create / update / typed refusals
# --------------------------------------------------------------------------------------


def test_upsert_creates_then_updates_same_row(db: Session, matter: Matter, attorney: User) -> None:
    doc = _add_document(db, matter, page_count=5)
    pick = ExhibitPickRequest(document_id=doc.id, include_pages=[3, 1, 1], excluded_pages=[2])
    ex = m.upsert_exhibit_pick(db, user=attorney, matter=matter, pick=pick)

    assert ex.include_pages == [1, 3]  # sorted + deduped
    assert ex.excluded_pages == [2]
    assert ex.phi_disposition == PhiDisposition.PENDING.value

    # A second pick on the same document UPDATES the row (unique (matter, document)).
    pick2 = ExhibitPickRequest(document_id=doc.id, include_pages=[4, 5], sort_order=2)
    ex2 = m.upsert_exhibit_pick(db, user=attorney, matter=matter, pick=pick2)
    assert ex2.id == ex.id
    assert ex2.include_pages == [4, 5]
    assert ex2.excluded_pages == []
    assert ex2.sort_order == 2
    assert db.scalar(select(Exhibit).where(Exhibit.matter_id == matter.id)) is not None
    assert len(list(db.scalars(select(Exhibit).where(Exhibit.matter_id == matter.id)))) == 1


def test_pick_page_out_of_range_is_typed(db: Session, matter: Matter, attorney: User) -> None:
    doc = _add_document(db, matter, page_count=3)
    pick = ExhibitPickRequest(document_id=doc.id, include_pages=[1, 4], excluded_pages=[7])
    with pytest.raises(m.InvalidPick) as excinfo:
        m.upsert_exhibit_pick(db, user=attorney, matter=matter, pick=pick)
    assert excinfo.value.reason == "page_out_of_range"
    assert "4" in excinfo.value.detail and "7" in excinfo.value.detail


def test_pick_include_exclude_overlap_is_typed(db: Session, matter: Matter, attorney: User) -> None:
    doc = _add_document(db, matter, page_count=5)
    pick = ExhibitPickRequest(document_id=doc.id, include_pages=[1, 2, 3], excluded_pages=[3, 4])
    with pytest.raises(m.InvalidPick) as excinfo:
        m.upsert_exhibit_pick(db, user=attorney, matter=matter, pick=pick)
    assert excinfo.value.reason == "include_exclude_overlap"
    assert "3" in excinfo.value.detail


def test_pick_cross_matter_document_is_typed(
    db: Session, matter: Matter, attorney: User, firm: Firm
) -> None:
    other = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Other",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.EVIDENCE_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(other)
    db.commit()
    foreign_doc = _add_document(db, other, filename="foreign.pdf")

    pick = ExhibitPickRequest(document_id=foreign_doc.id, include_pages=[1])
    with pytest.raises(m.InvalidPick) as excinfo:
        m.upsert_exhibit_pick(db, user=attorney, matter=matter, pick=pick)
    assert excinfo.value.reason == "document_not_in_matter"


def test_pick_stays_pending_with_open_third_party_phi_flag(
    db: Session, matter: Matter, attorney: User
) -> None:
    doc = _add_document(db, matter, page_count=5)
    # An undispositioned third_party_phi flag anchored to this document.
    flag = RiskFlag(
        matter_id=matter.id,
        kind=FlagKind.THIRD_PARTY_PHI.value,
        severity=FlagSeverity.HIGH.value,
        anchors=[{"document_id": str(doc.id), "page": 2}],
        detail="another patient's records on p2",
        disposition=None,
    )
    tenant_add(db, flag, matter.firm_id)
    db.commit()

    pick = ExhibitPickRequest(document_id=doc.id, include_pages=[1, 2])
    ex = m.upsert_exhibit_pick(db, user=attorney, matter=matter, pick=pick)
    assert ex.phi_disposition == PhiDisposition.PENDING.value


# --------------------------------------------------------------------------------------
# set_phi_disposition — attorney-only
# --------------------------------------------------------------------------------------


def test_phi_disposition_attorney_sets_value(db: Session, matter: Matter, attorney: User) -> None:
    doc = _add_document(db, matter)
    ex = m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[1]),
    )
    updated = m.set_phi_disposition(
        db, user=attorney, exhibit=ex, disposition=PhiDisposition.CLEARED
    )
    assert updated.phi_disposition == PhiDisposition.CLEARED.value


def test_phi_disposition_paralegal_forbidden(
    db: Session, matter: Matter, attorney: User, paralegal: User
) -> None:
    doc = _add_document(db, matter)
    ex = m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[1]),
    )
    with pytest.raises(m.PhiDispositionForbidden) as excinfo:
        m.set_phi_disposition(db, user=paralegal, exhibit=ex, disposition=PhiDisposition.CLEARED)
    assert excinfo.value.actual_role == UserRole.PARALEGAL.value
    # Unchanged after the refusal.
    db.refresh(ex)
    assert ex.phi_disposition == PhiDisposition.PENDING.value


# --------------------------------------------------------------------------------------
# build_draft_manifest — ordering + integrity verdicts + blocking
# --------------------------------------------------------------------------------------


def test_manifest_orders_by_sort_order_then_filename(
    db: Session, matter: Matter, attorney: User
) -> None:
    doc_a = _add_document(db, matter, filename="zebra.pdf", page_count=3)
    doc_b = _add_document(db, matter, filename="alpha.pdf", page_count=3)
    doc_c = _add_document(db, matter, filename="mid.pdf", page_count=3)
    # Same sort_order for a + b (tie broken by filename); c earlier by sort_order.
    m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc_a.id, include_pages=[1], sort_order=5),
    )
    m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc_b.id, include_pages=[1], sort_order=5),
    )
    m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc_c.id, include_pages=[1], sort_order=1),
    )

    manifest = m.build_draft_manifest(db, matter=matter)
    filenames = [e.filename for e in manifest.entries]
    assert filenames == ["mid.pdf", "alpha.pdf", "zebra.pdf"]


def test_manifest_integrity_empty_include(db: Session, matter: Matter, attorney: User) -> None:
    doc = _add_document(db, matter, page_count=3)
    # Excluded-only pick: nothing to collate.
    m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[], excluded_pages=[1]),
    )
    manifest = m.build_draft_manifest(db, matter=matter)
    assert manifest.entries[0].integrity == "empty_include"
    # Empty-include entry is blocked (integrity != ok) but NOT for pending PHI (no includes).
    assert any("empty_include" in b for b in manifest.blocking)
    assert not any("pending PHI" in b for b in manifest.blocking)


def test_manifest_integrity_page_out_of_range_after_shrink(
    db: Session, matter: Matter, attorney: User
) -> None:
    doc = _add_document(db, matter, page_count=5)
    m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[4, 5]),
    )
    # The document later shrinks (e.g. a re-ingest with fewer pages) — the pick now points past EOF.
    doc.page_count = 3
    db.commit()

    manifest = m.build_draft_manifest(db, matter=matter)
    assert manifest.entries[0].integrity == "page_out_of_range"
    assert manifest.entries[0].page_count == 3


def test_manifest_integrity_doc_superseded(db: Session, matter: Matter, attorney: User) -> None:
    doc = _add_document(db, matter, page_count=3)
    m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[1, 2]),
    )
    decision = DedupDecision(
        matter_id=matter.id,
        document_id=doc.id,
        status=DedupStatus.DUPLICATE_OF.value,
        resolution=DedupResolution.SUPERSEDED.value,
    )
    tenant_add(db, decision, matter.firm_id)
    db.commit()

    manifest = m.build_draft_manifest(db, matter=matter)
    assert manifest.entries[0].integrity == "doc_superseded"
    assert any("doc_superseded" in b for b in manifest.blocking)


def test_manifest_pending_phi_blocks_only_entries_with_includes(
    db: Session, matter: Matter, attorney: User
) -> None:
    doc = _add_document(db, matter, filename="with_includes.pdf", page_count=3)
    m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=[1, 2]),
    )
    # phi_disposition defaults pending; the entry has includes -> it is blocked for PHI.
    manifest = m.build_draft_manifest(db, matter=matter)
    entry = manifest.entries[0]
    assert entry.integrity == "ok"
    assert entry.phi_disposition == "pending"
    assert any("pending PHI" in b for b in manifest.blocking)

    # Clear the PHI -> no longer blocking.
    ex = db.scalar(select(Exhibit).where(Exhibit.document_id == doc.id))
    m.set_phi_disposition(db, user=attorney, exhibit=ex, disposition=PhiDisposition.CLEARED)
    manifest2 = m.build_draft_manifest(db, matter=matter)
    assert manifest2.blocking == ()


# --------------------------------------------------------------------------------------
# build_draft_manifest — minting
# --------------------------------------------------------------------------------------


def _cleared_pick(
    db: Session,
    matter: Matter,
    attorney: User,
    doc: CaseDocument,
    *,
    pages: list[int],
    sort_order: int = 0,
) -> Exhibit:
    ex = m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(document_id=doc.id, include_pages=pages, sort_order=sort_order),
    )
    return m.set_phi_disposition(db, user=attorney, exhibit=ex, disposition=PhiDisposition.CLEARED)


def test_manifest_mint_stamps_tokens_and_is_idempotent(
    db: Session, matter: Matter, attorney: User
) -> None:
    doc_a = _add_document(db, matter, filename="a.pdf", page_count=3)
    doc_b = _add_document(db, matter, filename="b.pdf", page_count=3)
    _cleared_pick(db, matter, attorney, doc_a, pages=[1, 2], sort_order=1)
    _cleared_pick(db, matter, attorney, doc_b, pages=[1], sort_order=2)

    manifest = m.build_draft_manifest(db, matter=matter, mint_tokens=True)
    tokens = [e.exhibit_token for e in manifest.entries]
    assert tokens == ["[[EX_1]]", "[[EX_2]]"]
    # Display forms carry the 1-based ordinal + filename.
    ex_tokens = list(
        db.scalars(
            select(FactToken).where(
                FactToken.matter_id == matter.id, FactToken.kind == TokenKind.EXHIBIT.value
            )
        )
    )
    displays = {
        r.token_id: r.display_form
        for r in ex_tokens
        if r.registry_version == matter.registry_version
    }
    assert displays == {"EX_1": "Exhibit 1 — a.pdf", "EX_2": "Exhibit 2 — b.pdf"}
    version_after_first = matter.registry_version

    # Re-mint with identical picks: token ids stable, no new version.
    manifest2 = m.build_draft_manifest(db, matter=matter, mint_tokens=True)
    assert [e.exhibit_token for e in manifest2.entries] == ["[[EX_1]]", "[[EX_2]]"]
    assert matter.registry_version == version_after_first


def test_manifest_mint_skips_non_ok_integrity(db: Session, matter: Matter, attorney: User) -> None:
    good = _add_document(db, matter, filename="good.pdf", page_count=3)
    empty = _add_document(db, matter, filename="empty.pdf", page_count=3)
    _cleared_pick(db, matter, attorney, good, pages=[1], sort_order=1)
    # Excluded-only -> empty_include integrity -> not minted.
    ex = m.upsert_exhibit_pick(
        db,
        user=attorney,
        matter=matter,
        pick=ExhibitPickRequest(
            document_id=empty.id, include_pages=[], excluded_pages=[1], sort_order=2
        ),
    )
    m.set_phi_disposition(db, user=attorney, exhibit=ex, disposition=PhiDisposition.CLEARED)

    manifest = m.build_draft_manifest(db, matter=matter, mint_tokens=True)
    by_doc = {e.document_id: e for e in manifest.entries}
    assert by_doc[good.id].exhibit_token == "[[EX_1]]"
    assert by_doc[empty.id].exhibit_token is None  # non-ok entry not minted


def test_manifest_no_mint_when_flag_false(db: Session, matter: Matter, attorney: User) -> None:
    doc = _add_document(db, matter, page_count=3)
    _cleared_pick(db, matter, attorney, doc, pages=[1])
    manifest = m.build_draft_manifest(db, matter=matter, mint_tokens=False)
    assert manifest.entries[0].exhibit_token is None
    # No EX tokens exist.
    assert (
        db.scalar(
            select(FactToken).where(
                FactToken.matter_id == matter.id, FactToken.kind == TokenKind.EXHIBIT.value
            )
        )
        is None
    )


# --------------------------------------------------------------------------------------
# mint_exhibits (registry unit) — idempotency, kind, source_ref, interleaving
# --------------------------------------------------------------------------------------


def _ex_entry(doc_id: uuid.UUID, display: str, pages: list[int]) -> dict:
    return {
        "key": str(doc_id),
        "display_form": display,
        "anchors": [{"document_id": str(doc_id), "page": p} for p in pages],
    }


def test_mint_exhibits_kind_source_ref_and_value(db: Session, matter: Matter) -> None:
    doc_id = uuid.uuid4()
    outcome = registry.mint_exhibits(
        db, matter=matter, entries=[_ex_entry(doc_id, "Exhibit 1 — x.pdf", [1, 2])]
    )
    assert (outcome.minted, outcome.updated, outcome.unchanged) == (1, 0, 0)
    assert outcome.bumped is True

    row = db.scalar(
        select(FactToken).where(
            FactToken.matter_id == matter.id, FactToken.kind == TokenKind.EXHIBIT.value
        )
    )
    assert row is not None
    assert row.token_id == "EX_1"
    assert row.source == TokenSource.ATTORNEY.value
    assert row.status == TokenStatus.VERIFIED.value
    assert row.source_ref == f"exhibit:{doc_id}"
    assert row.value == {"document_id": str(doc_id), "included_pages": [1, 2]}
    assert row.anchors == [
        {"document_id": str(doc_id), "page": 1},
        {"document_id": str(doc_id), "page": 2},
    ]
    version = db.scalar(
        select(RegistryVersion).where(
            RegistryVersion.matter_id == matter.id, RegistryVersion.version == 1
        )
    )
    assert version.change_reason == "exhibit_sync"


def test_mint_exhibits_idempotent(db: Session, matter: Matter) -> None:
    doc_id = uuid.uuid4()
    entry = _ex_entry(doc_id, "Exhibit 1 — x.pdf", [1])
    registry.mint_exhibits(db, matter=matter, entries=[entry])
    second = registry.mint_exhibits(db, matter=matter, entries=[entry])
    assert (second.minted, second.updated, second.unchanged) == (0, 0, 1)
    assert second.bumped is False
    assert second.version == 1


def test_mint_exhibits_supersedes_on_page_change(db: Session, matter: Matter) -> None:
    doc_id = uuid.uuid4()
    registry.mint_exhibits(db, matter=matter, entries=[_ex_entry(doc_id, "Exhibit 1 — x.pdf", [1])])
    outcome = registry.mint_exhibits(
        db, matter=matter, entries=[_ex_entry(doc_id, "Exhibit 1 — x.pdf", [1, 2])]
    )
    assert (outcome.minted, outcome.updated, outcome.unchanged) == (0, 1, 0)
    rows = list(
        db.scalars(
            select(FactToken).where(FactToken.matter_id == matter.id, FactToken.token_id == "EX_1")
        )
    )
    assert {r.registry_version for r in rows} == {1, 2}


def test_mint_exhibits_interleaves_with_fact_and_amt_ordinals(db: Session, matter: Matter) -> None:
    # A FACT minted first, then an AMT, then an EX — all share the one matter namespace.
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.MEDICAL_RECORD.value,
        source_label="records.pdf",
        filename="records.pdf",
        page_count=3,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    enc = MedicalEncounter(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        date_of_service=dt.date(2026, 1, 10),
        provider="Dr. A",
        facility="General",
        encounter_type="ER",
        complaints=[],
        findings=[],
        diagnoses=[],
        procedures=[],
        work_status=None,
        narrative_tokenized="",
        anchors=[{"document_id": str(doc.id), "page": 1}],
        merged_from=[],
        field_confidence={},
    )
    db.add(enc)
    db.commit()

    registry.sync_extracted_facts(db, matter=matter)  # FACT_1 @ v1
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[
            AmountFact(
                key="specials.grand.billed",
                value_cents=100,
                display_form="$1.00",
                ledger_ref={"line_ids": [], "category": None, "column": "billed"},
                ledger_hash="h",
            )
        ],
    )  # AMT_2 @ v2
    registry.mint_exhibits(
        db, matter=matter, entries=[_ex_entry(doc.id, "Exhibit 1 — records.pdf", [1])]
    )  # EX_3 @ v3

    ex = db.scalar(
        select(FactToken).where(
            FactToken.matter_id == matter.id, FactToken.kind == TokenKind.EXHIBIT.value
        )
    )
    assert ex.token_id == "EX_3"
    assert ex.registry_version == 3
