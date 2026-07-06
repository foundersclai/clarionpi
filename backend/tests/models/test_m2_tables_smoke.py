"""Smoke test for the M2 tables: one row of each new table via ``tenant_add`` + uniqueness guards.

Builds the full schema on in-memory SQLite, inserts a firm/matter/document/encounter and one row
of every new M2 table (``extraction_runs``, ``registry_versions``, ``chronology_row_overlays``)
through ``tenant_add`` so the tenancy write door is exercised. Asserts each table's unique
constraint rejects a duplicate, and that the new ``FactToken`` columns round-trip.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.tenancy import tenant_add
from app.models import enums, orm


@pytest.fixture
def session() -> Iterator[Session]:
    engine = sa.create_engine("sqlite://")
    orm.Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def _seed(
    session: Session,
) -> tuple[uuid.UUID, orm.Matter, orm.CaseDocument, orm.MedicalEncounter]:
    firm_id = uuid.uuid4()
    session.add(orm.Firm(id=firm_id, name="Acme Injury Law"))
    matter = orm.Matter(
        firm_id=firm_id,
        client_display_name="Jane Roe",
        claim_type=enums.ClaimType.MVA.value,
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=enums.GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    session.add(matter)
    session.flush()
    doc = orm.CaseDocument(
        firm_id=firm_id,
        matter_id=matter.id,
        doc_type=enums.DocType.MEDICAL_RECORD.value,
        source_label="upload",
        filename="records.pdf",
        storage_key="matters/x/records.pdf",
        page_count=20,
        dedup_status=enums.DedupStatus.UNIQUE.value,
        status=enums.DocStatus.OCR_DONE.value,
        needs_review=False,
    )
    session.add(doc)
    session.flush()
    encounter = orm.MedicalEncounter(
        firm_id=firm_id,
        matter_id=matter.id,
        date_of_service=dt.date(2026, 1, 16),
        provider="Dr. Smith",
        facility="Mercy General",
        encounter_type="er",
        anchors=[{"document_id": str(doc.id), "page": 3}],
        field_confidence={"provider": 0.95},
        merge_basis=enums.MergeBasis.DETERMINISTIC_KEY.value,
    )
    session.add(encounter)
    session.flush()
    return firm_id, matter, doc, encounter


def test_insert_one_row_of_each_new_m2_table_via_tenant_add(session: Session) -> None:
    firm_id, matter, doc, encounter = _seed(session)

    run = orm.ExtractionRun(
        matter_id=matter.id,
        document_id=doc.id,
        window_id=f"{doc.id}:1-8",
        window_start=1,
        window_end=8,
        prompt_version="v1",
        model="claude-sonnet-5",
        status=enums.ExtractionStatus.OK.value,
        rows_emitted=3,
        anchors_rejected=1,
    )
    tenant_add(session, run, firm_id)

    version = orm.RegistryVersion(
        matter_id=matter.id,
        version=1,
        frozen=False,
        parent_version=0,
        change_reason="initial sync",
    )
    tenant_add(session, version, firm_id)

    overlay = orm.ChronologyRowOverlay(
        matter_id=matter.id,
        encounter_id=encounter.id,
        edited_fields={"work_status": "light duty"},
        base_hash_at_edit="abc123",
        status=enums.OverlayStatus.APPLIED.value,
        actor_id=None,
    )
    tenant_add(session, overlay, firm_id)
    session.commit()

    assert session.query(orm.ExtractionRun).count() == 1
    assert session.query(orm.RegistryVersion).count() == 1
    assert session.query(orm.ChronologyRowOverlay).count() == 1


def test_extraction_run_uniqueness_rejects_same_doc_window_prompt(session: Session) -> None:
    firm_id, matter, doc, _encounter = _seed(session)
    for _ in range(2):
        session.add(
            orm.ExtractionRun(
                firm_id=firm_id,
                matter_id=matter.id,
                document_id=doc.id,
                window_id=f"{doc.id}:1-8",
                window_start=1,
                window_end=8,
                prompt_version="v1",  # same (doc, window, prompt) — must be rejected
                model="claude-sonnet-5",
                status=enums.ExtractionStatus.OK.value,
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_registry_version_uniqueness_rejects_same_matter_version(session: Session) -> None:
    firm_id, matter, _doc, _encounter = _seed(session)
    for _ in range(2):
        session.add(
            orm.RegistryVersion(
                firm_id=firm_id,
                matter_id=matter.id,
                version=1,  # same (matter, version) — must be rejected
                frozen=False,
                change_reason="",
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_chronology_overlay_uniqueness_rejects_same_matter_encounter(session: Session) -> None:
    firm_id, matter, _doc, encounter = _seed(session)
    for _ in range(2):
        session.add(
            orm.ChronologyRowOverlay(
                firm_id=firm_id,
                matter_id=matter.id,
                encounter_id=encounter.id,  # same (matter, encounter) — must be rejected
                edited_fields={},
                base_hash_at_edit="h",
                status=enums.OverlayStatus.APPLIED.value,
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_new_fact_token_columns_round_trip(session: Session) -> None:
    firm_id, matter, _doc, _encounter = _seed(session)
    token = orm.FactToken(
        firm_id=firm_id,
        matter_id=matter.id,
        token_id="AMT_1",
        registry_version=1,
        kind=enums.TokenKind.AMOUNT.value,
        value={"cents": 123456},
        display_form="$1,234.56",
        anchors=[],
        status=enums.TokenStatus.VERIFIED.value,
        source=enums.TokenSource.RULES.value,
        source_ref="amt:specials.grand.billed",
        ledger_ref={"line_ids": ["a", "b"], "category": None, "column": "billed"},
        snapshot_value_cents=123456,
        ledger_hash="deadbeefcafe",
    )
    session.add(token)
    session.commit()

    reloaded = session.get(orm.FactToken, token.id)
    assert reloaded is not None
    assert reloaded.source_ref == "amt:specials.grand.billed"
    assert reloaded.ledger_ref == {"line_ids": ["a", "b"], "category": None, "column": "billed"}
    assert reloaded.snapshot_value_cents == 123456
    assert reloaded.ledger_hash == "deadbeefcafe"


def test_medical_encounter_new_columns_round_trip(session: Session) -> None:
    _firm_id, _matter, _doc, encounter = _seed(session)
    reloaded = session.get(orm.MedicalEncounter, encounter.id)
    assert reloaded is not None
    assert reloaded.field_confidence == {"provider": 0.95}
    assert reloaded.merge_basis == enums.MergeBasis.DETERMINISTIC_KEY.value


def test_billing_line_reconciliation_defaults_to_llm_only(session: Session) -> None:
    firm_id, matter, _doc, _encounter = _seed(session)
    line = orm.BillingLine(
        firm_id=firm_id,
        matter_id=matter.id,
        provider="Imaging Center",
        date_of_service=dt.date(2026, 1, 17),
        billed_cents=50000,
        category=enums.LedgerCategory.IMAGING.value,
        anchor={"document_id": str(uuid.uuid4()), "page": 1},
    )
    session.add(line)
    session.commit()
    reloaded = session.get(orm.BillingLine, line.id)
    assert reloaded is not None
    assert reloaded.reconciliation == enums.ReconciliationStatus.LLM_ONLY.value
