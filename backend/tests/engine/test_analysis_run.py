"""Analysis-run tests (M4 Wave C) — the ``analysis_running -> evidence_review`` build over SSE.

Self-contained (mirrors ``tests/engine/test_risk_engine.py`` / ``test_chronology.py``): builds its
own in-memory engine, firm/user/matter parked at ``analysis_running``, and seeds encounters + a
billing line via direct ORM. A :class:`~app.core.llm_provider.ScriptedProvider` drives the two
metered stages the runner composes in order — chronology narratives (one call per empty-narrative
encounter) then the risk-labeling pass (one call) — behind a real
:class:`~app.core.llm_telemetry.MeteredLLMClient`, so metering is exercised end to end. Synthetic
data only — no PHI.

Coverage: happy-path FRAME ORDER + the matter landing at evidence_review + exact AnalysisSummary
numbers + run-log events; the NullProvider degrade (narratives skipped, risk LLM skipped, still
completes + advances); re-entrancy (an unexpected error mid-run leaves the state unchanged and a
re-run completes); and the unregistered-claims logging path.
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
from app.core.matter_logs import MatterRunLogger
from app.engine.brain1 import analysis as analysis_module
from app.engine.brain1 import risk as risk_module
from app.engine.brain1.analysis import AnalysisSummary, run_analysis
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    LedgerCategory,
    SseEvent,
)
from app.models.orm import (
    AuditEvent,
    BillingLine,
    CaseDocument,
    Firm,
    Matter,
    MedicalEncounter,
    RiskFlag,
    User,
)

# --------------------------------------------------------------------------------------
# Fixtures — in-memory engine + firm/user/matter, direct ORM (test_risk_engine shape)
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
def user(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="attorney@firm.test",
        display_name="Test Attorney",
        role="attorney",
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def matter(db: Session, firm: Firm) -> Matter:
    m = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Jane Doe",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.ANALYSIS_RUNNING.value,
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
    narrative: str = "",
) -> MedicalEncounter:
    enc = MedicalEncounter(
        id=uuid.uuid4(),
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
        narrative_tokenized=narrative,
        anchors=anchors,
        merged_from=[],
        field_confidence={},
    )
    db.add(enc)
    db.flush()
    return enc


def _make_billing_line(
    db: Session, matter: Matter, doc: CaseDocument, *, billed: int
) -> BillingLine:
    line = BillingLine(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        provider="General Hospital",
        date_of_service=dt.date(2026, 1, 1),
        billed_cents=billed,
        category=LedgerCategory.ER.value,
        anchor=_anchor(doc.id, 1),
    )
    db.add(line)
    db.flush()
    return line


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=50, output_tokens=20, cost_cents=1)


def _narrative(body: str) -> CompletionResult:
    return _completion(json.dumps({"narrative": body}))


def _risk_batch_json(page: int) -> CompletionResult:
    return _completion(
        json.dumps(
            {
                "flags": [
                    {
                        "kind": "preexisting_condition",
                        "severity": "high",
                        "detail": "prior neck injury noted",
                        "anchor_pages": [page],
                    }
                ]
            }
        )
    )


def _parse_frames(frames: list[str]) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for frame in frames:
        lines = frame.strip().split("\n")
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        parsed.append((event, data))
    return parsed


def _log_events(logger: MatterRunLogger) -> list[str]:
    return [json.loads(line)["event"] for line in logger.path.read_text().splitlines()]


# --------------------------------------------------------------------------------------
# Happy path — frame order, gate advance, exact summary, run-log events
# --------------------------------------------------------------------------------------


def test_happy_path_frame_order_advances_and_summary(
    db: Session, matter: Matter, user: User, tmp_path
) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 10), anchors=[_anchor(doc.id, 5)])
    _make_billing_line(db, matter, doc, billed=10_000)
    db.commit()

    logger = MatterRunLogger(matter.id, "analysis", logs_dir=tmp_path)
    # Order the runner calls the provider: one narrative (one empty-narrative encounter), then the
    # risk-labeling pass. The risk label cites page 5 (a valid encounter anchor).
    provider = ScriptedProvider(
        [_narrative("At [[FACT_1]], neck pain reported."), _risk_batch_json(5)]
    )
    frames = list(run_analysis(db, matter=matter, user=user, provider=provider, run_logger=logger))
    parsed = _parse_frames(frames)
    names = [name for name, _ in parsed]

    # Frame ORDER: started -> 4 step frames -> gate_ready -> completed.
    assert names == [
        SseEvent.STATUS.value,  # started
        SseEvent.STATUS.value,  # step: registry_sync
        SseEvent.STATUS.value,  # step: chronology
        SseEvent.STATUS.value,  # step: ledger
        SseEvent.STATUS.value,  # step: risk_flags
        SseEvent.GATE_READY.value,
        SseEvent.STATUS.value,  # completed
    ]
    assert parsed[0][1] == {"phase": "analysis", "state": "started", "matter_id": str(matter.id)}
    steps = [d["step"] for _, d in parsed[1:5]]
    assert steps == ["registry_sync", "chronology", "ledger", "risk_flags"]
    gate_ready = parsed[5][1]
    assert gate_ready == {"gate": "evidence_review", "matter_id": str(matter.id)}

    # Matter advanced to evidence_review.
    db.refresh(matter)
    assert matter.gate_state == GateState.EVIDENCE_REVIEW.value

    # AnalysisSummary numbers exact (carried on the completed frame).
    completed = parsed[6][1]
    assert completed["state"] == "completed"
    assert completed["phase"] == "analysis"
    assert completed["chronology_rows"] == 1
    assert completed["narratives_generated"] == 1
    assert completed["narratives_skipped"] == 0
    assert completed["unregistered_claims"] == 0
    assert completed["overlay_conflicts"] == 0
    assert completed["ledger_grand_billed_cents"] == 10_000
    assert completed["amounts_minted"] >= 1  # grand billed + demand basis AMTs
    assert completed["facts_synced"] == 1  # one encounter FACT minted
    assert completed["flags_deterministic"] == 0  # single encounter, no gap
    assert completed["flags_llm"] == 1
    assert completed["flags_llm_skipped"] is False
    assert completed["gate_advanced"] is True
    assert completed["registry_version"] >= 1

    # A risk flag persisted, and the audit + run-log record the completion.
    flags = list(db.execute(select(RiskFlag).where(RiskFlag.matter_id == matter.id)).scalars())
    assert len(flags) == 1
    kinds = list(db.scalars(select(AuditEvent.event_kind)))
    assert "analysis_completed" in kinds
    events = _log_events(logger)
    assert events[0] == "run_started"
    assert "gate_advanced" in events
    assert events[-1] == "run_completed"
    # started..completed order holds in the log too.
    assert events.index("registry_synced") < events.index("chronology_built")
    assert events.index("chronology_built") < events.index("ledger_amounts_minted")
    assert events.index("ledger_amounts_minted") < events.index("risk_flags_generated")


# --------------------------------------------------------------------------------------
# NullProvider degrade — narratives skipped, risk llm skipped, still completes + advances
# --------------------------------------------------------------------------------------


def test_null_provider_degrades_but_completes_and_advances(
    db: Session, matter: Matter, user: User, tmp_path
) -> None:
    doc = _make_document(db, matter)
    # Two encounters 40 days apart -> one deterministic gap flag (no LLM needed for that).
    _make_encounter(db, matter, dos=dt.date(2026, 1, 1), anchors=[_anchor(doc.id, 1)])
    _make_encounter(db, matter, dos=dt.date(2026, 2, 10), anchors=[_anchor(doc.id, 2)])
    _make_billing_line(db, matter, doc, billed=25_000)
    db.commit()

    logger = MatterRunLogger(matter.id, "analysis", logs_dir=tmp_path)
    frames = list(
        run_analysis(db, matter=matter, user=user, provider=NullProvider(), run_logger=logger)
    )
    parsed = _parse_frames(frames)
    completed = parsed[-1][1]

    # Completed + advanced even with no live provider.
    assert completed["state"] == "completed"
    db.refresh(matter)
    assert matter.gate_state == GateState.EVIDENCE_REVIEW.value

    # Narratives skipped (provider offline), risk LLM skipped, deterministic still ran.
    assert completed["narratives_generated"] == 0
    assert completed["narratives_skipped"] == 2  # both encounters skipped visibly
    assert completed["flags_llm_skipped"] is True
    assert completed["flags_llm"] == 0
    assert completed["flags_deterministic"] == 1  # the treatment-gap flag stands
    assert completed["ledger_grand_billed_cents"] == 25_000
    # No ERROR frame.
    assert all(name != SseEvent.ERROR.value for name, _ in parsed)


# --------------------------------------------------------------------------------------
# Re-entrancy — unexpected error mid-run leaves state unchanged; re-run completes
# --------------------------------------------------------------------------------------


def test_unexpected_error_mid_run_preserves_state_then_reruns(
    db: Session, matter: Matter, user: User, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _make_document(db, matter)
    _make_encounter(db, matter, dos=dt.date(2026, 1, 10), anchors=[_anchor(doc.id, 5)])
    _make_billing_line(db, matter, doc, billed=10_000)
    db.commit()

    logger = MatterRunLogger(matter.id, "analysis", logs_dir=tmp_path)

    # Force an UNEXPECTED failure in the risk step (a bug the composed stages did not absorb).
    def _boom(*args, **kwargs):
        raise RuntimeError("risk detector blew up")

    monkeypatch.setattr(analysis_module, "run_risk_detectors", _boom)
    frames = list(
        run_analysis(db, matter=matter, user=user, provider=NullProvider(), run_logger=logger)
    )
    parsed = _parse_frames(frames)

    # The stream ends with a single ERROR frame (no raw traceback), and the state did NOT move.
    assert parsed[-1][0] == SseEvent.ERROR.value
    assert parsed[-1][1]["error"] == "RuntimeError"
    assert parsed[-1][1]["phase"] == "analysis"
    db.refresh(matter)
    assert matter.gate_state == GateState.ANALYSIS_RUNNING.value  # unchanged — no gate advance
    assert "run_error" in _log_events(logger)

    # Re-POST: the failure is gone; the run resumes and completes, advancing the gate. Earlier
    # steps' per-step commits (registry sync, ledger mint) already landed, so the re-run finishes.
    monkeypatch.undo()
    provider = ScriptedProvider([_narrative("At [[FACT_1]], neck pain."), _risk_batch_json(5)])
    frames2 = list(run_analysis(db, matter=matter, user=user, provider=provider, run_logger=logger))
    parsed2 = _parse_frames(frames2)
    assert parsed2[-1][1]["state"] == "completed"
    db.refresh(matter)
    assert matter.gate_state == GateState.EVIDENCE_REVIEW.value


# --------------------------------------------------------------------------------------
# Unregistered-claims logging path (forced via a direct narrative write to a bad token)
# --------------------------------------------------------------------------------------


def test_unregistered_claim_is_logged_and_counted_but_still_completes(
    db: Session, matter: Matter, user: User, tmp_path, caplog
) -> None:
    doc = _make_document(db, matter)
    # Pre-seed a narrative that references a token the registry will never mint ([[FACT_99]]). The
    # narrative is non-empty, so chronology never regenerates it — it is scanned as-is and the
    # unregistered token surfaces (a G3 block downstream; here it is logged + counted, not raised).
    _make_encounter(
        db,
        matter,
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 5)],
        narrative="At [[FACT_99]], an unregistered reference.",
    )
    _make_billing_line(db, matter, doc, billed=10_000)
    db.commit()

    logger = MatterRunLogger(matter.id, "analysis", logs_dir=tmp_path)
    # No narrative call needed (the narrative already exists); the risk pass is the only LLM use.
    provider = ScriptedProvider([_risk_batch_json(5)])
    with caplog.at_level("ERROR"):
        frames = list(
            run_analysis(db, matter=matter, user=user, provider=provider, run_logger=logger)
        )
    parsed = _parse_frames(frames)
    completed = parsed[-1][1]

    # Still completes + advances; the unregistered claim is counted on the summary and ERROR-logged.
    assert completed["state"] == "completed"
    assert completed["unregistered_claims"] == 1
    db.refresh(matter)
    assert matter.gate_state == GateState.EVIDENCE_REVIEW.value
    assert any("unregistered" in rec.message.lower() for rec in caplog.records)


# --------------------------------------------------------------------------------------
# Public-name / summary-shape guard (the API + FE waves are specced against these)
# --------------------------------------------------------------------------------------


def test_public_surface_names_are_stable() -> None:
    assert analysis_module._PHASE == "analysis"
    # The runner composes the landed risk surface under its documented name.
    assert risk_module.run_risk_detectors is analysis_module.run_risk_detectors
    assert AnalysisSummary.__dataclass_fields__.keys() == {
        "chronology_rows",
        "narratives_generated",
        "narratives_skipped",
        "unregistered_claims",
        "overlay_conflicts",
        "ledger_grand_billed_cents",
        "amounts_minted",
        "facts_synced",
        "flags_deterministic",
        "flags_llm",
        "flags_llm_skipped",
        "registry_version",
        "gate_advanced",
    }
