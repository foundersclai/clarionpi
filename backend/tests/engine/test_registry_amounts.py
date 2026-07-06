"""Fact-registry AMT tests — ledger_sync minting, shared-ordinal interleaving, re-verification.

Self-contained (own in-memory engine + firm/matter, direct ORM), matching
``test_registry.py``'s fixture style. Exercises the money -> registry AMT boundary: the
registry stores ``[[AMT]]`` values + ledger hash and re-verifies at render, but never computes.
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
from app.engine.tokenizer import registry
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    TokenKind,
    TokenSource,
    TokenStatus,
)
from app.models.orm import (
    CaseDocument,
    FactToken,
    Firm,
    Matter,
    MedicalEncounter,
    RegistryVersion,
)
from app.models.schemas import AmountFact

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
def matter(db: Session, firm: Firm) -> Matter:
    m = Matter(
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
    db.add(m)
    db.commit()
    return m


def _amount(
    key: str, cents: int, *, display: str, ledger_hash: str, line_ids: list[str] | None = None
) -> AmountFact:
    return AmountFact(
        key=key,
        value_cents=cents,
        display_form=display,
        ledger_ref={"line_ids": line_ids or [], "category": None, "column": "billed"},
        ledger_hash=ledger_hash,
    )


def _amt_tokens(db: Session, matter: Matter) -> list[FactToken]:
    return list(
        db.execute(
            select(FactToken)
            .where(FactToken.matter_id == matter.id, FactToken.kind == TokenKind.AMOUNT.value)
            .order_by(FactToken.registry_version, FactToken.token_id)
        ).scalars()
    )


# --------------------------------------------------------------------------------------
# mint_amounts — mint / idempotency / supersession
# --------------------------------------------------------------------------------------


def test_mint_amounts_creates_amt_tokens(db: Session, matter: Matter) -> None:
    amounts = [
        _amount("specials.grand.billed", 250000, display="$2,500.00", ledger_hash="h_grand"),
        _amount("specials.category.imaging.billed", 80000, display="$800.00", ledger_hash="h_img"),
    ]
    outcome = registry.mint_amounts(db, matter=matter, amounts=amounts)

    assert (outcome.minted, outcome.updated, outcome.unchanged) == (2, 0, 0)
    assert outcome.bumped is True
    assert outcome.version == 1

    rows = _amt_tokens(db, matter)
    assert [r.token_id for r in rows] == ["AMT_1", "AMT_2"]
    by_id = {r.token_id: r for r in rows}
    grand = by_id["AMT_1"]
    assert grand.status == TokenStatus.VERIFIED.value
    assert grand.source == TokenSource.EXTRACTOR.value
    assert grand.snapshot_value_cents == 250000
    assert grand.ledger_hash == "h_grand"
    assert grand.ledger_ref == {"line_ids": [], "category": None, "column": "billed"}
    assert grand.value == {"cents": 250000}


def test_mint_amounts_reason_is_ledger_sync(db: Session, matter: Matter) -> None:
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 100, display="$1.00", ledger_hash="h")],
    )
    version = db.execute(
        select(RegistryVersion).where(
            RegistryVersion.matter_id == matter.id, RegistryVersion.version == 1
        )
    ).scalar_one()
    assert version.change_reason == "ledger_sync"


def test_mint_amounts_is_idempotent(db: Session, matter: Matter) -> None:
    amounts = [_amount("specials.grand.billed", 250000, display="$2,500.00", ledger_hash="h1")]
    registry.mint_amounts(db, matter=matter, amounts=amounts)
    second = registry.mint_amounts(db, matter=matter, amounts=amounts)

    assert (second.minted, second.updated, second.unchanged) == (0, 0, 1)
    assert second.bumped is False
    assert second.version == 1
    assert len(_amt_tokens(db, matter)) == 1


def test_mint_amounts_supersedes_on_value_change(db: Session, matter: Matter) -> None:
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 250000, display="$2,500.00", ledger_hash="h1")],
    )
    outcome = registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 300000, display="$3,000.00", ledger_hash="h2")],
    )

    assert (outcome.minted, outcome.updated, outcome.unchanged) == (0, 1, 0)
    assert outcome.version == 2
    rows = [r for r in _amt_tokens(db, matter) if r.token_id == "AMT_1"]
    assert {r.registry_version for r in rows} == {1, 2}
    v2 = next(r for r in rows if r.registry_version == 2)
    assert v2.snapshot_value_cents == 300000
    assert v2.value == {"cents": 300000}


def test_amt_ordinals_continue_shared_matter_namespace(db: Session, matter: Matter) -> None:
    # A FACT is minted first; the AMT then takes the NEXT shared ordinal (interleaving proof).
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

    registry.sync_extracted_facts(db, matter=matter)  # -> FACT_1 at v1
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 100, display="$1.00", ledger_hash="h")],
    )  # -> AMT_2 at v2

    amt = _amt_tokens(db, matter)[0]
    assert amt.token_id == "AMT_2"
    assert amt.registry_version == 2


# --------------------------------------------------------------------------------------
# AMT re-verification at render
# --------------------------------------------------------------------------------------


def test_resolve_render_amt_ok_when_hash_matches(db: Session, matter: Matter) -> None:
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 250000, display="$2,500.00", ledger_hash="h1")],
    )
    result = registry.resolve_for_render(
        db, matter=matter, token="[[AMT_1]]", live_ledger_hash=lambda _ref: "h1"
    )
    assert result.outcome == "ok"
    assert result.display_form == "$2,500.00"
    assert result.value == {"cents": 250000}


def test_resolve_render_amt_mismatch_exposes_snapshot(db: Session, matter: Matter) -> None:
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 250000, display="$2,500.00", ledger_hash="h1")],
    )
    result = registry.resolve_for_render(
        db, matter=matter, token="[[AMT_1]]", live_ledger_hash=lambda _ref: "DIFFERENT"
    )
    assert result.outcome == "amt_mismatch"
    # Snapshot value still exposed, plus the mismatch flag.
    assert isinstance(result.value, dict)
    assert result.value["cents"] == 250000
    assert result.value["ledger_mismatch"] is True
    assert result.value["stored_ledger_hash"] == "h1"
    assert result.value["live_ledger_hash"] == "DIFFERENT"


def test_resolve_render_amt_ok_when_no_live_check(db: Session, matter: Matter) -> None:
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 250000, display="$2,500.00", ledger_hash="h1")],
    )
    result = registry.resolve_for_render(db, matter=matter, token="[[AMT_1]]")
    assert result.outcome == "ok"
    assert result.value == {"cents": 250000}


def test_resolve_render_disputed_status(db: Session, matter: Matter) -> None:
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[_amount("specials.grand.billed", 250000, display="$2,500.00", ledger_hash="h1")],
    )
    # Force the latest row to DISPUTED directly.
    row = db.execute(
        select(FactToken).where(FactToken.matter_id == matter.id, FactToken.token_id == "AMT_1")
    ).scalar_one()
    row.status = TokenStatus.DISPUTED.value
    db.add(row)
    db.commit()

    result = registry.resolve_for_render(
        db, matter=matter, token="[[AMT_1]]", live_ledger_hash=lambda _ref: "h1"
    )
    assert result.outcome == "disputed"
    assert result.display_form == "$2,500.00"
