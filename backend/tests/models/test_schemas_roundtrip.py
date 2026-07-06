"""Pydantic <- ORM round-trip + input-validation tests.

Constructs real ORM rows in an in-memory SQLite session, loads them through the Pydantic
mirrors (``from_attributes``), and asserts enum strings deserialize into real enum members.
Also checks that ``MatterCreate`` rejects an unsupported claim type and a bad jurisdiction type.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import enums, orm, schemas


@pytest.fixture
def session() -> Iterator[Session]:
    engine = sa.create_engine("sqlite://")
    orm.Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def _seed_firm_and_matter(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    firm_id = uuid.uuid4()
    session.add(orm.Firm(id=firm_id, name="Acme Injury Law"))
    matter = orm.Matter(
        firm_id=firm_id,
        client_display_name="Jane Roe",
        claim_type=enums.ClaimType.MVA.value,
        incident_date=date(2025, 1, 15),
        jurisdiction="CA",
        venue_county="Los Angeles",
        gate_state=enums.GateState.FACTS_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    session.add(matter)
    session.flush()
    return firm_id, matter.id


def test_matter_roundtrips_with_real_enums(session: Session) -> None:
    _firm_id, matter_id = _seed_firm_and_matter(session)
    row = session.get(orm.Matter, matter_id)
    assert row is not None

    model = schemas.Matter.model_validate(row)
    assert isinstance(model.gate_state, enums.GateState)
    assert model.gate_state is enums.GateState.FACTS_REVIEW
    assert isinstance(model.claim_type, enums.ClaimType)
    assert model.claim_type is enums.ClaimType.MVA
    assert model.jurisdiction == "CA"


def test_fact_token_roundtrips_with_real_enums(session: Session) -> None:
    firm_id, matter_id = _seed_firm_and_matter(session)
    token = orm.FactToken(
        firm_id=firm_id,
        matter_id=matter_id,
        token_id="FACT_12",
        registry_version=0,
        kind=enums.TokenKind.FACT.value,
        value={"note": "cervical strain"},
        display_form="cervical strain diagnosed 2025-01-15",
        anchors=[{"document_id": str(uuid.uuid4()), "page": 3, "bbox": None}],
        status=enums.TokenStatus.VERIFIED.value,
        source=enums.TokenSource.EXTRACTOR.value,
    )
    session.add(token)
    session.flush()

    model = schemas.FactToken.model_validate(session.get(orm.FactToken, token.id))
    assert model.kind is enums.TokenKind.FACT
    assert model.status is enums.TokenStatus.VERIFIED
    assert model.source is enums.TokenSource.EXTRACTOR
    assert model.token_id == "FACT_12"
    assert len(model.anchors) == 1
    assert isinstance(model.anchors[0], schemas.PageAnchor)
    assert model.anchors[0].page == 3


def test_gate_record_roundtrips_with_real_enums(session: Session) -> None:
    firm_id, matter_id = _seed_firm_and_matter(session)
    actor_id = uuid.uuid4()
    session.add(
        orm.User(
            id=actor_id,
            firm_id=firm_id,
            email="attorney@acme.example",
            display_name="A. Attorney",
            role=enums.UserRole.ATTORNEY.value,
        )
    )
    record = orm.GateRecord(
        firm_id=firm_id,
        matter_id=matter_id,
        gate="facts_review",
        action=enums.GateAction.APPROVE.value,
        actor_id=actor_id,
        actor_role=enums.UserRole.ATTORNEY.value,
        payload_hash="deadbeef",
        idempotency_key="idem-1",
    )
    session.add(record)
    session.flush()

    model = schemas.GateRecord.model_validate(session.get(orm.GateRecord, record.id))
    assert model.action is enums.GateAction.APPROVE
    assert model.actor_role is enums.UserRole.ATTORNEY
    assert model.idempotency_key == "idem-1"


def test_matter_create_accepts_valid_input() -> None:
    payload = schemas.MatterCreate(
        client_display_name="Jane Roe",
        claim_type=enums.ClaimType.MVA,
        incident_date=date(2025, 1, 15),
        jurisdiction="CA",
        venue_county=None,
    )
    assert payload.claim_type is enums.ClaimType.MVA


def test_matter_create_rejects_unsupported_claim_type() -> None:
    with pytest.raises(ValidationError):
        schemas.MatterCreate(
            client_display_name="Jane Roe",
            claim_type="slipfall",  # type: ignore[arg-type]
            incident_date=date(2025, 1, 15),
            jurisdiction="CA",
        )


def test_matter_create_rejects_bad_jurisdiction_type() -> None:
    with pytest.raises(ValidationError):
        schemas.MatterCreate(
            client_display_name="Jane Roe",
            claim_type=enums.ClaimType.MVA,
            incident_date=date(2025, 1, 15),
            jurisdiction=["not", "a", "string"],  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------------------
# M2 extraction I/O contracts (pure Pydantic value objects)
# --------------------------------------------------------------------------------------


def test_page_anchor_carries_optional_extraction_fields() -> None:
    anchor = schemas.PageAnchor(
        document_id=uuid.uuid4(), page=4, window_id="doc:1-8", field="provider"
    )
    assert anchor.window_id == "doc:1-8"
    assert anchor.field == "provider"


def test_page_anchor_rejects_page_below_one() -> None:
    with pytest.raises(ValidationError):
        schemas.PageAnchor(document_id=uuid.uuid4(), page=0)


def test_extracted_encounter_requires_nonempty_anchor_pages() -> None:
    with pytest.raises(ValidationError):
        schemas.ExtractedEncounter(
            date_of_service=date(2026, 1, 16),
            provider="Dr. Smith",
            encounter_type="er",
            anchor_pages=[],  # min_length=1 — must be rejected
        )


def test_extracted_encounter_batch_defaults_empty() -> None:
    batch = schemas.ExtractedEncounterBatch()
    assert batch.encounters == []


def test_extracted_billing_line_validates_category_enum() -> None:
    line = schemas.ExtractedBillingLine(
        provider="Imaging Center",
        date_of_service=date(2026, 1, 17),
        billed="$1,234.56",
        category=enums.LedgerCategory.IMAGING,
        anchor_page=2,
    )
    assert line.category is enums.LedgerCategory.IMAGING
    with pytest.raises(ValidationError):
        schemas.ExtractedBillingLine(
            provider="X",
            date_of_service=date(2026, 1, 17),
            billed="$1.00",
            category="not_a_category",  # type: ignore[arg-type]
            anchor_page=1,
        )


def test_amount_fact_value_cents_is_non_negative() -> None:
    fact = schemas.AmountFact(
        key="specials.grand.billed",
        value_cents=123456,
        display_form="$1,234.56",
        ledger_ref={"line_ids": ["a"], "category": None, "column": "billed"},
        ledger_hash="deadbeef",
    )
    assert fact.value_cents == 123456
    with pytest.raises(ValidationError):
        schemas.AmountFact(
            key="specials.grand.billed",
            value_cents=-1,  # Cents alias is ge=0
            display_form="-",
            ledger_ref={},
            ledger_hash="h",
        )
