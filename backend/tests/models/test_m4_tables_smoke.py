"""Smoke test for the M4 model changes: RiskFlag columns + Exhibit reshape + M4 schemas.

Builds the full schema on in-memory SQLite and exercises:

* the new ``RiskFlag`` columns (``detector`` default, ``disposition_role``) round-tripping,
* a full-shape ``Exhibit`` insert via ``tenant_add`` (incl. ``matter_id``) with defaults, and the
  ``(matter_id, document_id)`` unique constraint rejecting a duplicate,
* the new enum value sets (``FlagDetector`` / ``PhiDisposition``),
* the ``FlagDispositionRequest`` omit-requires-rationale validator, and
* ``ExhibitPickRequest`` page ``ge=1`` validation.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.core.tenancy import tenant_add
from app.models import enums, orm, schemas

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = sa.create_engine("sqlite://")
    orm.Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def _seed(session: Session) -> tuple[uuid.UUID, orm.Matter, orm.CaseDocument]:
    firm_id = uuid.uuid4()
    session.add(orm.Firm(id=firm_id, name="Acme Injury Law"))
    matter = orm.Matter(
        firm_id=firm_id,
        client_display_name="Jane Roe",
        claim_type=enums.ClaimType.MVA.value,
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=enums.GateState.EVIDENCE_REVIEW.value,
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
    return firm_id, matter, doc


# --------------------------------------------------------------------------------------
# RiskFlag new columns
# --------------------------------------------------------------------------------------


def test_risk_flag_new_columns_round_trip_with_defaults(session: Session) -> None:
    firm_id, matter, _doc = _seed(session)
    flag = orm.RiskFlag(
        firm_id=firm_id,
        matter_id=matter.id,
        kind=enums.FlagKind.TREATMENT_GAP.value,
        severity=enums.FlagSeverity.HIGH.value,
        anchors=[],
        detail="42-day gap between PT visits",
    )
    session.add(flag)
    session.commit()

    reloaded = session.get(orm.RiskFlag, flag.id)
    assert reloaded is not None
    # detector defaults to FlagDetector.LABEL; disposition_role is nullable.
    assert reloaded.detector == enums.FlagDetector.LABEL.value
    assert reloaded.disposition_role is None


def test_risk_flag_detector_and_role_persist_explicit_values(session: Session) -> None:
    firm_id, matter, _doc = _seed(session)
    flag = orm.RiskFlag(
        firm_id=firm_id,
        matter_id=matter.id,
        kind=enums.FlagKind.LOW_PROPERTY_DAMAGE.value,
        severity=enums.FlagSeverity.MEDIUM.value,
        anchors=[],
        detail="property damage under threshold",
        detector=enums.FlagDetector.HEURISTIC_LLM.value,
        disposition=enums.FlagDisposition.OMIT_WITH_RATIONALE.value,
        disposition_role=enums.UserRole.ATTORNEY.value,
        disposition_rationale="de minimis; not worth raising",
    )
    session.add(flag)
    session.commit()

    reloaded = session.get(orm.RiskFlag, flag.id)
    assert reloaded is not None
    assert reloaded.detector == enums.FlagDetector.HEURISTIC_LLM.value
    assert reloaded.disposition_role == enums.UserRole.ATTORNEY.value


# --------------------------------------------------------------------------------------
# Exhibit reshape
# --------------------------------------------------------------------------------------


def test_exhibit_full_shape_insert_via_tenant_add(session: Session) -> None:
    firm_id, matter, doc = _seed(session)
    exhibit = orm.Exhibit(
        matter_id=matter.id,
        document_id=doc.id,
        exhibit_no=1,
        include_pages=[1, 2, 3],
        excluded_pages=[4],
        sort_order=0,
    )
    tenant_add(session, exhibit, firm_id)
    session.commit()

    reloaded = session.get(orm.Exhibit, exhibit.id)
    assert reloaded is not None
    assert reloaded.matter_id == matter.id
    assert reloaded.firm_id == firm_id
    assert reloaded.include_pages == [1, 2, 3]
    assert reloaded.excluded_pages == [4]
    # phi_disposition defaults to PENDING (blocks the M5 binder build); sort_order defaults 0.
    assert reloaded.phi_disposition == enums.PhiDisposition.PENDING.value
    assert reloaded.sort_order == 0


def test_exhibit_unique_matter_document_rejects_duplicate(session: Session) -> None:
    firm_id, matter, doc = _seed(session)
    for _ in range(2):
        session.add(
            orm.Exhibit(
                firm_id=firm_id,
                matter_id=matter.id,
                document_id=doc.id,  # same (matter, document) — must be rejected
                include_pages=[1],
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


# --------------------------------------------------------------------------------------
# New enums
# --------------------------------------------------------------------------------------


def test_flag_detector_values() -> None:
    assert {d.value for d in enums.FlagDetector} == {"date_math", "label", "heuristic_llm"}


def test_phi_disposition_values() -> None:
    assert {d.value for d in enums.PhiDisposition} == {"pending", "cleared", "excluded"}


# --------------------------------------------------------------------------------------
# Schema validators
# --------------------------------------------------------------------------------------


def test_flag_disposition_omit_without_rationale_rejected() -> None:
    with pytest.raises(ValidationError):
        schemas.FlagDispositionRequest(disposition=enums.FlagDisposition.OMIT_WITH_RATIONALE)
    # blank/whitespace rationale is also rejected.
    with pytest.raises(ValidationError):
        schemas.FlagDispositionRequest(
            disposition=enums.FlagDisposition.OMIT_WITH_RATIONALE, rationale="   "
        )


def test_flag_disposition_omit_with_rationale_ok() -> None:
    req = schemas.FlagDispositionRequest(
        disposition=enums.FlagDisposition.OMIT_WITH_RATIONALE,
        rationale="de minimis; not raising",
    )
    assert req.disposition is enums.FlagDisposition.OMIT_WITH_RATIONALE


def test_flag_disposition_address_without_rationale_ok() -> None:
    req = schemas.FlagDispositionRequest(disposition=enums.FlagDisposition.ADDRESS_IN_LETTER)
    assert req.rationale is None


def test_exhibit_pick_request_page_ge_one() -> None:
    ok = schemas.ExhibitPickRequest(document_id=uuid.uuid4(), include_pages=[1, 2])
    assert ok.include_pages == [1, 2]
    with pytest.raises(ValidationError):
        schemas.ExhibitPickRequest(document_id=uuid.uuid4(), include_pages=[0])
    with pytest.raises(ValidationError):
        schemas.ExhibitPickRequest(document_id=uuid.uuid4(), excluded_pages=[0])


def test_billing_line_edit_batch_requires_at_least_one_edit() -> None:
    with pytest.raises(ValidationError):
        schemas.BillingLineEditBatch(edits=[])


def test_risk_label_output_requires_anchor_pages() -> None:
    with pytest.raises(ValidationError):
        schemas.RiskLabelOutput(
            kind=enums.FlagKind.DEGENERATIVE_FINDING,
            severity=enums.FlagSeverity.LOW,
            detail="degenerative disc noted",
            anchor_pages=[],
        )


# --------------------------------------------------------------------------------------
# Migration 0006 round-trip
# --------------------------------------------------------------------------------------


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _columns(db_url: str, table: str) -> set[str]:
    engine = sa.create_engine(db_url)
    inspector = sa.inspect(engine)
    cols = {c["name"] for c in inspector.get_columns(table)}
    engine.dispose()
    return cols


def test_migration_0006_up_down_up_round_trip(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "m4_roundtrip.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)

    # up to head: the M4 columns exist.
    command.upgrade(cfg, "head")
    risk_cols = _columns(db_url, "risk_flags")
    exhibit_cols = _columns(db_url, "exhibits")
    assert {"detector", "disposition_role"} <= risk_cols
    assert {"matter_id", "excluded_pages", "phi_disposition", "sort_order", "updated_at"} <= (
        exhibit_cols
    )

    # down one revision (0006 -> 0005): the M4 columns are gone.
    command.downgrade(cfg, "0005_gate_service")
    risk_cols_down = _columns(db_url, "risk_flags")
    exhibit_cols_down = _columns(db_url, "exhibits")
    assert "detector" not in risk_cols_down
    assert "disposition_role" not in risk_cols_down
    for col in ("matter_id", "excluded_pages", "phi_disposition", "sort_order", "updated_at"):
        assert col not in exhibit_cols_down

    # up again: idempotent re-application succeeds and restores the columns.
    command.upgrade(cfg, "head")
    assert {"detector", "disposition_role"} <= _columns(db_url, "risk_flags")
    assert {"matter_id", "excluded_pages", "phi_disposition", "sort_order", "updated_at"} <= (
        _columns(db_url, "exhibits")
    )
