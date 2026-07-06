"""Strategy-plan emit tests (M5 Wave B1) — deterministic allocator + Opus emphasis + versioning.

Self-contained (mirrors ``tests/engine/test_analysis_run.py``): an in-memory engine + firm/matter,
encounters / an incident / billing lines seeded via direct ORM, then the REAL registry sync
(``sync_extracted_facts`` + ``mint_amounts``) to mint FACT/AMT tokens with their true source_refs —
so the allocator is exercised against the same token shapes production produces. Synthetic data
only — no PHI.

Coverage: the per-section allocation is EXACT for the AZ five-section skeleton (allowed pools by
kind + required subsets by source_ref); the Opus emphasis pass (scripted -> directives; degraded ->
empty); the plan version increments across emits; and ``LetterStructureMissing`` propagates when a
pack has no letter skeleton.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.llm_provider import CompletionResult, NullProvider, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.engine.brain2.plan import emit_strategy_plan
from app.engine.tokenizer import registry
from app.models.enums import GateState, LedgerCategory, TokenKind
from app.models.orm import (
    BillingLine,
    CaseDocument,
    FactToken,
    Firm,
    IncidentFacts,
    Matter,
    MedicalEncounter,
    StrategyInputs,
    StrategyPlan,
)
from app.money.assemble import compute_matter_ledger
from app.money.specials import amounts_for_registry
from app.rules.errors import LetterStructureMissing
from app.rules.loader import load_pack

# --------------------------------------------------------------------------------------
# Fixtures — in-memory engine + firm/matter (test_analysis_run shape)
# --------------------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=100000,
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
        gate_state=GateState.PLAN_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()
    return m


def _anchor(document_id: uuid.UUID, page: int = 1) -> dict:
    return {"document_id": str(document_id), "page": page}


def _make_document(db: Session, matter: Matter) -> CaseDocument:
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type="medical_record",
        source_label="records.pdf",
        filename="records.pdf",
        page_count=20,
        dedup_status="unique",
        status="extracted",
    )
    db.add(doc)
    db.flush()
    return doc


def _make_encounter(db: Session, matter: Matter, *, dos: dt.date, anchors: list[dict]) -> uuid.UUID:
    enc_id = uuid.uuid4()
    db.add(
        MedicalEncounter(
            id=enc_id,
            firm_id=matter.firm_id,
            matter_id=matter.id,
            date_of_service=dos,
            provider="Dr. A",
            facility="General Hospital",
            encounter_type="PT",
            complaints=["neck pain"],
            findings=[],
            diagnoses=["whiplash"],
            procedures=[],
            work_status=None,
            narrative_tokenized="",
            anchors=anchors,
            merged_from=[],
            field_confidence={},
        )
    )
    db.flush()
    return enc_id


def _make_incident(db: Session, matter: Matter, doc: CaseDocument) -> None:
    db.add(
        IncidentFacts(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            payload={"location": "1st & Main", "narrative": "rear-end collision"},
            anchors=[_anchor(doc.id, 1)],
        )
    )
    db.flush()


def _make_billing_line(
    db: Session, matter: Matter, doc: CaseDocument, *, billed: int, category: str
) -> None:
    db.add(
        BillingLine(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            provider="General Hospital",
            date_of_service=dt.date(2026, 1, 1),
            billed_cents=billed,
            category=category,
            anchor=_anchor(doc.id, 1),
        )
    )
    db.flush()


def _seed_facts_and_amounts(db: Session, matter: Matter) -> CaseDocument:
    """Seed 2 encounters + an incident + 2 bills, then run the real registry sync + AMT mint.

    Returns the document. After this the matter carries FACT tokens (2 encounter + 1 incident) and
    AMT tokens (the grand-billed + demand-basis ledger slots) at their production source_refs.
    """
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 12), anchors=[_anchor(doc.id, 5)])
    _make_encounter(db, matter, dos=dt.date(2026, 2, 2), anchors=[_anchor(doc.id, 6)])
    _make_incident(db, matter, doc)
    _make_billing_line(db, matter, doc, billed=10_000, category=LedgerCategory.ER.value)
    _make_billing_line(db, matter, doc, billed=5_000, category=LedgerCategory.IMAGING.value)
    db.commit()

    registry.sync_extracted_facts(db, matter=matter)
    pack = load_pack(matter.jurisdiction)
    ledger = compute_matter_ledger(db, matter=matter, pack=pack)
    registry.mint_amounts(db, matter=matter, amounts=amounts_for_registry(ledger))
    db.refresh(matter)
    return doc


def _sections_by_id(plan: StrategyPlan) -> dict[str, dict]:
    return {s["section_id"]: s for s in plan.sections}


def _fact_ids(db: Session, matter: Matter, prefix: str) -> list[str]:
    rows = list(
        db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id, FactToken.kind == TokenKind.FACT.value
            )
        ).scalars()
    )
    return sorted(r.token_id for r in rows if r.source_ref and r.source_ref.startswith(prefix))


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=40, output_tokens=15, cost_cents=1)


# --------------------------------------------------------------------------------------
# Deterministic allocator — exact allowed/required per the AZ five sections
# --------------------------------------------------------------------------------------


def test_allocator_exactness_per_section(db: Session, matter: Matter) -> None:
    _seed_facts_and_amounts(db, matter)
    plan = emit_strategy_plan(db, None, matter=matter)
    sections = _sections_by_id(plan)

    all_facts = [
        r.token_id
        for r in db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id, FactToken.kind == TokenKind.FACT.value
            )
        ).scalars()
    ]
    all_amts = [
        r.token_id
        for r in db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id, FactToken.kind == TokenKind.AMOUNT.value
            )
        ).scalars()
    ]
    incident_id = _fact_ids(db, matter, "incident:")[0]
    encounter_ids = _fact_ids(db, matter, "encounter:")

    def _amt_by_ref(ref: str) -> str:
        row = db.execute(
            select(FactToken).where(FactToken.matter_id == matter.id, FactToken.source_ref == ref)
        ).scalar_one()
        return row.token_id

    grand = _amt_by_ref("amt:specials.grand.billed")
    demand_basis = _amt_by_ref("amt:specials.demand_basis")

    # intro_and_representation: NO tokens allowed, none required.
    intro = sections["intro_and_representation"]
    assert intro["allowed_tokens"] == []
    assert intro["required_tokens"] == []

    # liability: all FACTs allowed; the incident FACT required.
    liability = sections["liability"]
    assert set(liability["allowed_tokens"]) == set(all_facts)
    assert liability["required_tokens"] == [incident_id]

    # injuries_and_treatment: all FACTs allowed; up to first 3 encounter FACTs required (here 2).
    injuries = sections["injuries_and_treatment"]
    assert set(injuries["allowed_tokens"]) == set(all_facts)
    assert injuries["required_tokens"] == encounter_ids[:3]

    # damages_and_specials: all AMTs allowed; grand-billed + demand-basis required.
    damages = sections["damages_and_specials"]
    assert set(damages["allowed_tokens"]) == set(all_amts)
    assert damages["required_tokens"] == [grand, demand_basis]

    # demand_and_deadline: all AMTs allowed; demand-basis required.
    demand = sections["demand_and_deadline"]
    assert set(demand["allowed_tokens"]) == set(all_amts)
    assert demand["required_tokens"] == [demand_basis]


def test_allocator_required_encounters_capped_at_three(db: Session, matter: Matter) -> None:
    # Five encounters -> injuries_and_treatment requires only the FIRST THREE by minted ordinal.
    doc = _make_document(db, matter)
    for i in range(5):
        _make_encounter(db, matter, dos=dt.date(2026, 1, 10 + i), anchors=[_anchor(doc.id, 5 + i)])
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)
    db.refresh(matter)

    plan = emit_strategy_plan(db, None, matter=matter)
    injuries = _sections_by_id(plan)["injuries_and_treatment"]
    encounter_ids = _fact_ids(db, matter, "encounter:")
    assert len(encounter_ids) == 5
    assert injuries["required_tokens"] == encounter_ids[:3]


# --------------------------------------------------------------------------------------
# Emphasis — scripted directives, and the degraded (NullProvider / None) empty path
# --------------------------------------------------------------------------------------


def test_emphasis_scripted(db: Session, matter: Matter, firm: Firm) -> None:
    db.add(
        StrategyInputs(
            id=uuid.uuid4(),
            firm_id=firm.id,
            matter_id=matter.id,
            liability_theory="clear rear-end liability",
            injury_framing="cervical strain with PT",
            emphasis_notes="lead with liability",
            venue_posture="plaintiff-friendly county",
            anchor_amount_cents=None,
        )
    )
    db.commit()
    _seed_facts_and_amounts(db, matter)

    provider = ScriptedProvider(
        [_completion(json.dumps({"emphasis_directives": ["Foreground the rear-end liability."]}))]
    )
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    plan = emit_strategy_plan(db, client, matter=matter)
    assert plan.emphasis_directives == ["Foreground the rear-end liability."]
    # The emphasis prompt carried the verbatim attorney input.
    assert "clear rear-end liability" in provider.calls[0][2]


def test_emphasis_degraded_null_provider_is_empty(db: Session, matter: Matter) -> None:
    _seed_facts_and_amounts(db, matter)
    client = MeteredLLMClient(NullProvider(), db, matter.firm_id, matter.id)
    plan = emit_strategy_plan(db, client, matter=matter)
    assert plan.emphasis_directives == []


def test_emphasis_none_client_is_empty(db: Session, matter: Matter) -> None:
    _seed_facts_and_amounts(db, matter)
    plan = emit_strategy_plan(db, None, matter=matter)
    assert plan.emphasis_directives == []


# --------------------------------------------------------------------------------------
# Demand carry-forward + versioning + fail-loud on a missing skeleton
# --------------------------------------------------------------------------------------


def test_demand_amount_carried_from_strategy_inputs(
    db: Session, matter: Matter, firm: Firm
) -> None:
    db.add(
        StrategyInputs(
            id=uuid.uuid4(),
            firm_id=firm.id,
            matter_id=matter.id,
            liability_theory="",
            injury_framing="",
            emphasis_notes="",
            venue_posture="",
            anchor_amount_cents=125000,
        )
    )
    db.commit()
    _seed_facts_and_amounts(db, matter)
    plan = emit_strategy_plan(db, None, matter=matter)
    assert plan.demand_amount_cents == 125000
    assert plan.demand_type == "open"


def test_version_increments_across_emits(db: Session, matter: Matter) -> None:
    _seed_facts_and_amounts(db, matter)
    first = emit_strategy_plan(db, None, matter=matter)
    second = emit_strategy_plan(db, None, matter=matter)
    assert first.version == 1
    assert second.version == 2
    # Both rows persisted (never an overwrite).
    count = len(
        list(
            db.execute(select(StrategyPlan.id).where(StrategyPlan.matter_id == matter.id)).scalars()
        )
    )
    assert count == 2


def test_emit_registry_version_binds_matter(db: Session, matter: Matter) -> None:
    _seed_facts_and_amounts(db, matter)
    plan = emit_strategy_plan(db, None, matter=matter)
    assert plan.registry_version == matter.registry_version
    assert plan.approved is False


def test_letter_structure_missing_propagates(db: Session, firm: Firm, monkeypatch) -> None:
    # A pack with no letter_structure block -> LetterStructureMissing propagates (fail loud).
    m = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="No Skeleton",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.PLAN_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()

    from app.rules import loader as loader_module

    real_pack = load_pack("AZ")
    real_pack.letter_structure = None  # simulate a pack missing the skeleton
    # plan.py resolves ``load_pack`` from ``app.rules.loader`` at call time (a function-local
    # import), so patching the name in that module namespace is what the emit sees.
    monkeypatch.setattr(loader_module, "load_pack", lambda _j: real_pack)

    with pytest.raises(LetterStructureMissing):
        emit_strategy_plan(db, None, matter=m)
