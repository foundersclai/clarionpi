"""Risk-engine tests — deterministic detectors, LLM labeling, idempotent re-run, metering.

Self-contained: builds its own in-memory SQLite engine, firm/user/matter, and seeds encounters /
strategy / incident rows via direct ORM (the same shape as ``tests/engine/test_chronology.py``),
and drives the LLM labeling pass with a :class:`~app.core.llm_provider.ScriptedProvider` behind a
real :class:`~app.core.llm_telemetry.MeteredLLMClient` (so metering is exercised end to end).
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
from app.core.llm_provider import CompletionResult, ProviderNotConfigured, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.engine.brain1 import risk
from app.engine.brain1.risk import run_risk_detectors
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    FlagDetector,
    FlagDisposition,
    FlagKind,
    FlagSeverity,
    GateState,
)
from app.models.orm import (
    AuditEvent,
    CaseDocument,
    Firm,
    IncidentFacts,
    LlmCall,
    Matter,
    MedicalEncounter,
    RiskFlag,
    StrategyInputs,
)

# --------------------------------------------------------------------------------------
# Fixtures — in-memory engine + firm/user/matter, direct ORM
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
        gate_state=GateState.EVIDENCE_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()
    return m


def _make_document(db: Session, matter: Matter) -> CaseDocument:
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.MEDICAL_RECORD.value,
        source_label="records.pdf",
        filename="records.pdf",
        page_count=20,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    db.flush()
    return doc


def _anchor(document_id: uuid.UUID, page: int = 1) -> dict:
    return {"document_id": str(document_id), "page": page}


def _make_encounter(
    db: Session,
    matter: Matter,
    *,
    dos: dt.date,
    anchors: list[dict],
    encounter_type: str = "PT",
    diagnoses: list[str] | None = None,
    complaints: list[str] | None = None,
    findings: list[str] | None = None,
) -> MedicalEncounter:
    enc = MedicalEncounter(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        date_of_service=dos,
        provider="Dr. A",
        facility="General Hospital",
        encounter_type=encounter_type,
        complaints=complaints if complaints is not None else ["neck pain"],
        findings=findings if findings is not None else [],
        diagnoses=diagnoses if diagnoses is not None else ["whiplash"],
        procedures=[],
        work_status=None,
        narrative_tokenized="",
        anchors=anchors,
        merged_from=[],
        field_confidence={},
    )
    db.add(enc)
    db.flush()
    return enc


def _set_strategy(
    db: Session,
    matter: Matter,
    *,
    mmi_date: dt.date | None = None,
    property_damage_estimate_cents: int | None = None,
) -> StrategyInputs:
    row = StrategyInputs(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        mmi_date=mmi_date,
        property_damage_estimate_cents=property_damage_estimate_cents,
    )
    db.add(row)
    db.commit()
    return row


def _set_incident(
    db: Session, matter: Matter, *, anchors: list[dict], payload: dict
) -> IncidentFacts:
    row = IncidentFacts(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        payload=payload,
        anchors=anchors,
    )
    db.add(row)
    db.commit()
    return row


def _metered(db: Session, matter: Matter, provider: ScriptedProvider) -> MeteredLLMClient:
    return MeteredLLMClient(provider, db, matter.firm_id, matter.id)


def _batch(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=100, output_tokens=40, cost_cents=2)


def _flags(db: Session, matter: Matter, *, kind: FlagKind | None = None) -> list[RiskFlag]:
    stmt = select(RiskFlag).where(RiskFlag.matter_id == matter.id)
    if kind is not None:
        stmt = stmt.where(RiskFlag.kind == kind.value)
    return list(db.execute(stmt).scalars())


def _label_calls(db: Session, matter: Matter) -> list[LlmCall]:
    return list(
        db.execute(
            select(LlmCall).where(
                LlmCall.matter_id == matter.id, LlmCall.stage == "analysis.risk_flags"
            )
        ).scalars()
    )


# --------------------------------------------------------------------------------------
# treatment_gap — the gap grid
# --------------------------------------------------------------------------------------


def test_gap_at_exactly_threshold_does_not_flag_but_over_does(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    # 30-day gap (exactly threshold) -> NO flag; 31-day gap (> threshold) -> one flag.
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 1, 31), anchors=[_anchor(doc.id, 2)])  # 30d
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    assert outcome.deterministic_flags == 0
    assert _flags(db, matter, kind=FlagKind.TREATMENT_GAP) == []

    # Push the second encounter out to a 31-day gap (delete + re-seed the pair).
    for enc in db.execute(select(MedicalEncounter)).scalars().all():
        db.delete(enc)
    db.commit()
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 2, 1), anchors=[_anchor(doc.id, 2)])  # 31d
    db.commit()

    outcome = run_risk_detectors(db, None, matter=matter)
    assert outcome.deterministic_flags == 1
    gap_flags = _flags(db, matter, kind=FlagKind.TREATMENT_GAP)
    assert len(gap_flags) == 1
    assert gap_flags[0].severity == FlagSeverity.HIGH.value
    assert gap_flags[0].detector == FlagDetector.DATE_MATH.value
    assert "31 days" in gap_flags[0].detail
    assert "MMI not set" in gap_flags[0].detail  # MMI unset -> noted
    # Anchors are the union of the two bounding encounters.
    pages = sorted(a["page"] for a in gap_flags[0].anchors)
    assert pages == [1, 2]


def test_multiple_gaps_produce_multiple_flags(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    # Three encounters, two large gaps (40d and 50d) -> two flags.
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 2, 10), anchors=[_anchor(doc.id, 2)])  # 40d
    _make_encounter(db, matter, dos=dt.date(2026, 4, 1), anchors=[_anchor(doc.id, 3)])  # 50d
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    assert outcome.deterministic_flags == 2
    assert len(_flags(db, matter, kind=FlagKind.TREATMENT_GAP)) == 2


def test_mmi_cuts_post_mmi_gaps(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    # Gap 1 (40d) ends 2026-02-10 (pre-MMI); gap 2 (50d) ends 2026-04-01 (post-MMI).
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 2, 10), anchors=[_anchor(doc.id, 2)])
    _make_encounter(db, matter, dos=dt.date(2026, 4, 1), anchors=[_anchor(doc.id, 3)])
    _set_strategy(db, matter, mmi_date=dt.date(2026, 3, 1))  # between the two later encounters
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    # Only the pre-MMI gap counts; the post-MMI gap is expected.
    assert outcome.deterministic_flags == 1
    gap = _flags(db, matter, kind=FlagKind.TREATMENT_GAP)
    assert len(gap) == 1
    assert "MMI not set" not in gap[0].detail  # MMI IS set here


def test_single_encounter_has_no_gap(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    assert outcome.deterministic_flags == 0


# --------------------------------------------------------------------------------------
# low_property_damage
# --------------------------------------------------------------------------------------


def test_low_property_damage_below_threshold_flags(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _set_strategy(db, matter, property_damage_estimate_cents=100000)  # $1,000 < $1,500 default
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    lpd = _flags(db, matter, kind=FlagKind.LOW_PROPERTY_DAMAGE)
    assert len(lpd) == 1
    assert lpd[0].severity == FlagSeverity.MEDIUM.value
    assert lpd[0].detector == FlagDetector.DATE_MATH.value
    assert lpd[0].anchors == []  # intake-derived — no page anchor
    assert outcome.deterministic_flags == 1


def test_low_property_damage_at_threshold_does_not_flag(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _set_strategy(db, matter, property_damage_estimate_cents=150000)  # == threshold -> not below
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    assert _flags(db, matter, kind=FlagKind.LOW_PROPERTY_DAMAGE) == []
    assert outcome.deterministic_flags == 0


def test_low_property_damage_needs_an_encounter(db: Session, matter: Matter) -> None:
    # Below threshold but NO encounter -> no flag (no injury treatment to pair with).
    _set_strategy(db, matter, property_damage_estimate_cents=100000)
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    assert _flags(db, matter, kind=FlagKind.LOW_PROPERTY_DAMAGE) == []
    assert outcome.deterministic_flags == 0


def test_low_property_damage_unset_estimate_no_flag(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _set_strategy(db, matter, property_damage_estimate_cents=None)  # estimate unknown
    db.commit()
    run_risk_detectors(db, None, matter=matter)
    assert _flags(db, matter, kind=FlagKind.LOW_PROPERTY_DAMAGE) == []


# --------------------------------------------------------------------------------------
# Idempotent re-run discipline
# --------------------------------------------------------------------------------------


def test_rerun_is_idempotent_no_duplicates(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 2, 15), anchors=[_anchor(doc.id, 2)])  # 45d gap
    db.commit()

    first = run_risk_detectors(db, None, matter=matter)
    assert first.deterministic_flags == 1
    assert first.replaced_open == 0  # nothing existed before

    second = run_risk_detectors(db, None, matter=matter)
    assert second.deterministic_flags == 1  # re-derived fresh
    assert second.replaced_open == 1  # the prior open flag was replaced
    # No duplicate: still exactly one gap flag.
    assert len(_flags(db, matter, kind=FlagKind.TREATMENT_GAP)) == 1


def test_rerun_preserves_dispositioned_and_replaces_open(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    # Two gaps: 40d ending 2026-02-10, 60d ending 2026-04-11.
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 2, 10), anchors=[_anchor(doc.id, 2)])
    _make_encounter(db, matter, dos=dt.date(2026, 4, 11), anchors=[_anchor(doc.id, 3)])
    db.commit()

    run_risk_detectors(db, None, matter=matter)
    gaps = _flags(db, matter, kind=FlagKind.TREATMENT_GAP)
    assert len(gaps) == 2
    # Disposition ONE of them directly (attorney work).
    dispositioned = gaps[0]
    dispositioned.disposition = FlagDisposition.ADDRESS_IN_LETTER.value
    db.add(dispositioned)
    db.commit()
    kept_id = dispositioned.id

    outcome = run_risk_detectors(db, None, matter=matter)
    # The dispositioned flag is preserved (same row id); the other open one is replaced; the fresh
    # candidate matching the preserved one is skipped.
    assert outcome.replaced_open == 1  # only the undispositioned gap was open
    assert outcome.preserved_dispositioned == 1  # the fresh dup of the kept flag was skipped
    all_gaps = _flags(db, matter, kind=FlagKind.TREATMENT_GAP)
    assert len(all_gaps) == 2  # still exactly two, no duplicate
    assert any(f.id == kept_id for f in all_gaps)  # the dispositioned row survived unchanged
    # The preserved flag still carries its disposition.
    kept = next(f for f in all_gaps if f.id == kept_id)
    assert kept.disposition == FlagDisposition.ADDRESS_IN_LETTER.value


# --------------------------------------------------------------------------------------
# LLM labeling pass
# --------------------------------------------------------------------------------------


def _valid_batch_json(page: int) -> str:
    return (
        '{"flags": [{"kind": "preexisting_condition", "severity": "high", '
        f'"detail": "prior neck injury noted", "anchor_pages": [{page}]}}]}}'
    )


def test_llm_valid_batch_persists_flags_with_anchors(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        dos=dt.date(2026, 1, 1),
        anchors=[_anchor(doc.id, 5)],
        diagnoses=["cervical strain"],
    )
    db.commit()

    provider = ScriptedProvider([_batch(_valid_batch_json(5))])
    client = _metered(db, matter, provider)
    outcome = run_risk_detectors(db, client, matter=matter)

    assert outcome.llm_skipped is False
    assert outcome.llm_flags == 1
    assert outcome.anchors_rejected == 0
    flag = _flags(db, matter, kind=FlagKind.PREEXISTING_CONDITION)[0]
    assert flag.detector == FlagDetector.HEURISTIC_LLM.value
    assert flag.severity == FlagSeverity.HIGH.value
    assert flag.anchors == [{"document_id": str(doc.id), "page": 5}]
    # Exactly one metered call at the risk stage.
    assert len(_label_calls(db, matter)) == 1
    assert _label_calls(db, matter)[0].model == "claude-sonnet-5"


def test_llm_out_of_set_page_rejects_whole_label(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 5)])
    db.commit()

    # Model cites page 99 — not in the matter's valid page set -> whole label rejected.
    provider = ScriptedProvider([_batch(_valid_batch_json(99))])
    client = _metered(db, matter, provider)
    outcome = run_risk_detectors(db, client, matter=matter)

    assert outcome.llm_flags == 0
    assert outcome.anchors_rejected == 1
    assert _flags(db, matter, kind=FlagKind.PREEXISTING_CONDITION) == []


def test_llm_malformed_then_valid_retry_converges(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 5)])
    db.commit()

    provider = ScriptedProvider(
        [
            _batch("not json at all"),  # first attempt unparseable
            _batch(_valid_batch_json(5)),  # stricter retry valid
        ]
    )
    client = _metered(db, matter, provider)
    outcome = run_risk_detectors(db, client, matter=matter)

    assert outcome.llm_skipped is False
    assert outcome.llm_flags == 1
    assert len(provider.calls) == 2  # attempt + one stricter retry
    assert len(_label_calls(db, matter)) == 2  # both metered
    # The retry prompt insists on bare JSON.
    assert "no prose" in provider.calls[1][2]


def test_llm_two_parse_failures_is_skipped_deterministic_survives(
    db: Session, matter: Matter
) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 3, 1), anchors=[_anchor(doc.id, 2)])  # 59d gap
    db.commit()

    provider = ScriptedProvider([_batch("garbage"), _batch("still garbage")])
    client = _metered(db, matter, provider)
    outcome = run_risk_detectors(db, client, matter=matter)

    assert outcome.llm_skipped is True  # both attempts failed to parse
    assert outcome.llm_flags == 0
    assert outcome.deterministic_flags == 1  # the gap flag still stands
    assert len(_label_calls(db, matter)) == 2  # both attempts metered


def test_provider_none_skips_llm_but_produces_deterministic(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 3, 1), anchors=[_anchor(doc.id, 2)])
    db.commit()

    outcome = run_risk_detectors(db, None, matter=matter)  # client is None
    assert outcome.llm_skipped is True
    assert outcome.llm_flags == 0
    assert outcome.deterministic_flags == 1
    assert _label_calls(db, matter) == []  # never called the model


def test_provider_unavailable_skips_llm(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 3, 1), anchors=[_anchor(doc.id, 2)])
    db.commit()

    provider = ScriptedProvider([ProviderNotConfigured("no live provider")])
    client = _metered(db, matter, provider)
    outcome = run_risk_detectors(db, client, matter=matter)

    assert outcome.llm_skipped is True
    assert outcome.deterministic_flags == 1
    # The metered client still ledgered the failed attempt (zero cost).
    assert len(_label_calls(db, matter)) == 1
    assert _label_calls(db, matter)[0].cost_cents == 0


def test_llm_severity_clamped_to_taxonomy(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 3)])
    db.commit()

    # Model claims LOW severity for a preexisting_condition; the taxonomy says HIGH -> clamp.
    batch = (
        '{"flags": [{"kind": "preexisting_condition", "severity": "low", '
        '"detail": "prior injury", "anchor_pages": [3]}]}'
    )
    provider = ScriptedProvider([_batch(batch)])
    client = _metered(db, matter, provider)
    run_risk_detectors(db, client, matter=matter)

    flag = _flags(db, matter, kind=FlagKind.PREEXISTING_CONDITION)[0]
    assert flag.severity == FlagSeverity.HIGH.value  # clamped up from the model's "low"


def test_llm_incident_anchor_pages_are_valid(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    police = _make_document(db, matter)
    _set_incident(db, matter, anchors=[_anchor(police.id, 2)], payload={"at_fault": "unclear"})
    db.commit()

    # A liability_weakness citing the incident page 2 (valid via incident anchors).
    batch = (
        '{"flags": [{"kind": "liability_weakness", "severity": "high", '
        '"detail": "fault unclear in report", "anchor_pages": [2]}]}'
    )
    provider = ScriptedProvider([_batch(batch)])
    client = _metered(db, matter, provider)
    outcome = run_risk_detectors(db, client, matter=matter)

    assert outcome.anchors_rejected == 0
    lw = _flags(db, matter, kind=FlagKind.LIABILITY_WEAKNESS)
    assert len(lw) == 1
    # Page 2 exists on BOTH the police doc (incident anchor) — the stored anchor resolves it.
    assert {(a["document_id"], a["page"]) for a in lw[0].anchors} == {(str(police.id), 2)}


# --------------------------------------------------------------------------------------
# Audit + no-suppression
# --------------------------------------------------------------------------------------


def test_run_writes_audit_event_with_counts(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 3, 1), anchors=[_anchor(doc.id, 2)])
    db.commit()
    run_risk_detectors(db, None, matter=matter)

    events = list(
        db.execute(
            select(AuditEvent).where(AuditEvent.event_kind == "risk_flags_generated")
        ).scalars()
    )
    assert len(events) == 1
    assert events[0].payload["deterministic_flags"] == 1
    assert events[0].payload["llm_skipped"] is True


def test_per_kind_cap_is_not_suppression(db: Session, matter: Matter) -> None:
    # The default per-kind cap is 12; produce more than 12 gaps and assert ALL are surfaced
    # (the cap bounds UI display volume, never drops findings — inv 6).
    doc = _make_document(db, matter)
    # 15 encounters, each 40 days apart -> 14 gaps, all pre-any-MMI.
    day = dt.date(2026, 1, 1)
    for i in range(15):
        _make_encounter(db, matter, dos=day, anchors=[_anchor(doc.id, i + 1)])
        day = day + dt.timedelta(days=40)
    db.commit()
    outcome = run_risk_detectors(db, None, matter=matter)
    assert outcome.deterministic_flags == 14  # all 14 gaps, not capped at 12
    assert len(_flags(db, matter, kind=FlagKind.TREATMENT_GAP)) == 14


# --------------------------------------------------------------------------------------
# Public-name / stage-id guards (the analysis-run wave is specced against these)
# --------------------------------------------------------------------------------------


def test_public_surface_names_are_stable() -> None:
    assert risk._LABEL_STAGE == "analysis.risk_flags"
    assert risk._RUN_AUDIT_KIND == "risk_flags_generated"
    assert risk._DISPOSITION_AUDIT_KIND == "risk_flag_dispositioned"
    assert risk.RiskRunOutcome.__dataclass_fields__.keys() == {
        "deterministic_flags",
        "llm_flags",
        "anchors_rejected",
        "llm_skipped",
        "preserved_dispositioned",
        "replaced_open",
    }
