"""Chronology-builder tests — deterministic derived rows, overlays, tokens-only narratives.

Self-contained: builds its own in-memory SQLite engine, firm/user/matter, and encounter rows via
direct ORM (the same shape as ``tests/engine/test_registry.py``), runs
``registry.sync_extracted_facts`` so FACT tokens exist before narratives generate, and drives
narrative generation with a :class:`~app.core.llm_provider.ScriptedProvider` behind a real
:class:`~app.core.llm_telemetry.MeteredLLMClient` (so metering is exercised end to end).
"""

from __future__ import annotations

import datetime as dt
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.llm_provider import CompletionResult, ProviderNotConfigured, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.engine.brain1 import chronology
from app.engine.brain1.chronology import (
    ChronologyRow,
    base_hash_for,
    build_chronology,
    render_rows_for_wire,
    upsert_overlay,
)
from app.engine.tokenizer import registry
from app.engine.tokenizer.registry import SENTINEL, TOKEN_RE
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    OverlayStatus,
    UserRole,
)
from app.models.orm import (
    AuditEvent,
    CaseDocument,
    ChronologyRowOverlay,
    FactToken,
    Firm,
    LlmCall,
    Matter,
    MedicalEncounter,
    User,
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
def user(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="paralegal@firm.test",
        display_name="Test Paralegal",
        role=UserRole.PARALEGAL.value,
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
        gate_state=GateState.FACTS_REVIEW.value,
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
        page_count=5,
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
    provider: str,
    encounter_type: str,
    dos: dt.date,
    anchors: list[dict],
    diagnoses: list[str] | None = None,
    complaints: list[str] | None = None,
    created_at: dt.datetime | None = None,
    narrative: str = "",
) -> MedicalEncounter:
    enc = MedicalEncounter(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        date_of_service=dos,
        provider=provider,
        facility="General Hospital",
        encounter_type=encounter_type,
        complaints=complaints if complaints is not None else ["neck pain"],
        findings=[],
        diagnoses=diagnoses if diagnoses is not None else ["whiplash"],
        procedures=[],
        work_status=None,
        narrative_tokenized=narrative,
        anchors=anchors,
        merged_from=[],
        field_confidence={},
    )
    if created_at is not None:
        enc.created_at = created_at
    db.add(enc)
    db.flush()
    return enc


def _metered(db: Session, matter: Matter, provider: ScriptedProvider) -> MeteredLLMClient:
    return MeteredLLMClient(provider, db, matter.firm_id, matter.id)


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=50, output_tokens=20, cost_cents=1)


def _narrative_json(body: str) -> CompletionResult:
    return _completion(f'{{"narrative": "{body}"}}')


def _llm_calls(db: Session, matter: Matter) -> list[LlmCall]:
    return list(
        db.execute(
            select(LlmCall).where(
                LlmCall.matter_id == matter.id, LlmCall.stage == "chronology.narrative"
            )
        ).scalars()
    )


def _fact_token_for(db: Session, matter: Matter, encounter: MedicalEncounter) -> str:
    row = db.execute(
        select(FactToken).where(
            FactToken.matter_id == matter.id,
            FactToken.source_ref == f"encounter:{encounter.id}",
        )
    ).scalar_one()
    return f"[[{row.token_id}]]"


# --------------------------------------------------------------------------------------
# Deterministic derived rows
# --------------------------------------------------------------------------------------


def test_rows_ordered_and_row_id_is_encounter_id(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    # Insert out of chronological order; the builder must order by (DOS, created_at, id).
    e_mid = _make_encounter(
        db,
        matter,
        provider="Dr. B",
        encounter_type="PT",
        dos=dt.date(2026, 1, 20),
        anchors=[_anchor(doc.id, 2)],
        created_at=dt.datetime(2026, 2, 2, tzinfo=dt.UTC),
    )
    e_late = _make_encounter(
        db,
        matter,
        provider="Dr. C",
        encounter_type="ortho",
        dos=dt.date(2026, 1, 30),
        anchors=[_anchor(doc.id, 3)],
        created_at=dt.datetime(2026, 2, 3, tzinfo=dt.UTC),
    )
    e_early = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
        created_at=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
    )
    db.commit()

    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)

    assert [r.row_id for r in outcome.rows] == [str(e_early.id), str(e_mid.id), str(e_late.id)]
    assert [r.date_of_service for r in outcome.rows] == [
        dt.date(2026, 1, 10),
        dt.date(2026, 1, 20),
        dt.date(2026, 1, 30),
    ]
    assert [r.provider_display for r in outcome.rows] == ["Dr. A", "Dr. B", "Dr. C"]


def test_rebuild_is_identical_including_base_hash(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    for i, prov in enumerate(("Dr. A", "Dr. B", "Dr. C")):
        _make_encounter(
            db,
            matter,
            provider=prov,
            encounter_type="ER",
            dos=dt.date(2026, 1, 10 + i),
            anchors=[_anchor(doc.id, i + 1)],
            created_at=dt.datetime(2026, 2, 1 + i, tzinfo=dt.UTC),
        )
    db.commit()

    first = build_chronology(db, None, matter=matter, generate_narratives=False)
    second = build_chronology(db, None, matter=matter, generate_narratives=False)
    third = build_chronology(db, None, matter=matter, generate_narratives=False)

    assert first.rows == second.rows == third.rows
    assert [r.base_hash for r in first.rows] == [r.base_hash for r in third.rows]


# --------------------------------------------------------------------------------------
# base_hash_for
# --------------------------------------------------------------------------------------


def test_base_hash_stable_and_sensitive(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
        diagnoses=["whiplash"],
    )
    db.commit()

    h0 = base_hash_for(enc)
    assert base_hash_for(enc) == h0  # unchanged input -> stable

    enc.diagnoses = ["whiplash", "concussion"]
    db.flush()
    h1 = base_hash_for(enc)
    assert h1 != h0

    enc.diagnoses = ["whiplash"]
    enc.provider = "Dr. Z"
    db.flush()
    assert base_hash_for(enc) != h0

    enc.provider = "Dr. A"
    enc.narrative_tokenized = "At [[FACT_1]], neck pain."
    db.flush()
    assert base_hash_for(enc) != h0


# --------------------------------------------------------------------------------------
# Narrative generation — happy path + metering + non-regeneration
# --------------------------------------------------------------------------------------


def test_narrative_happy_path_persists_meters_and_skips_on_rebuild(
    db: Session, matter: Matter
) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)
    token = _fact_token_for(db, matter, enc)
    assert token == "[[FACT_1]]"

    provider = ScriptedProvider([_narrative_json(f"At {token}, the patient reported neck pain.")])
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)

    counts = (
        outcome.narratives_generated,
        outcome.narratives_skipped,
        outcome.narratives_failed,
    )
    assert counts == (1, 0, 0)
    db.refresh(enc)
    assert enc.narrative_tokenized == f"At {token}, the patient reported neck pain."
    # Metered: exactly one ledger row at the narrative stage, priced.
    calls = _llm_calls(db, matter)
    assert len(calls) == 1
    assert calls[0].model == "claude-sonnet-5"
    assert calls[0].cost_cents == 1
    assert outcome.unregistered_claims == ()

    # Second build with an EMPTY script: a non-empty narrative is never regenerated, so the
    # exhausted provider is never reached (no ProviderNotConfigured).
    provider2 = ScriptedProvider([])
    client2 = _metered(db, matter, provider2)
    outcome2 = build_chronology(db, client2, matter=matter)
    assert outcome2.narratives_generated == 0
    assert outcome2.narratives_skipped == 0
    assert provider2.calls == []


# --------------------------------------------------------------------------------------
# Validation gate — missing token
# --------------------------------------------------------------------------------------


def test_missing_token_retries_then_succeeds(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)
    token = _fact_token_for(db, matter, enc)

    provider = ScriptedProvider(
        [
            _narrative_json("The patient reported neck pain."),  # missing the token -> rejected
            _narrative_json(f"At {token}, the patient reported neck pain."),  # valid on regen
        ]
    )
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)

    assert outcome.narratives_generated == 1
    assert len(provider.calls) == 2  # attempt + one named-violation regeneration
    assert len(_llm_calls(db, matter)) == 2  # both attempts metered
    # The regeneration prompt names the violation.
    assert "previous attempt was rejected" in provider.calls[1][2]


def test_both_attempts_invalid_marks_failed_and_row_still_builds(
    db: Session, matter: Matter
) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)

    provider = ScriptedProvider(
        [
            _narrative_json("The patient reported neck pain."),  # no token
            _narrative_json("Still no token here at all."),  # still no token
        ]
    )
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)

    assert (outcome.narratives_generated, outcome.narratives_failed) == (0, 1)
    assert len(provider.calls) == 2
    db.refresh(enc)
    assert enc.narrative_tokenized == ""  # left empty
    # The row still builds (degrade-visible).
    assert len(outcome.rows) == 1
    assert outcome.rows[0].row_id == str(enc.id)
    assert outcome.rows[0].narrative_tokenized == ""


# --------------------------------------------------------------------------------------
# Raw-leak gate — provider name restated
# --------------------------------------------------------------------------------------


def test_raw_provider_name_leak_is_rejected(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. Alvarez",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)
    token = _fact_token_for(db, matter, enc)

    provider = ScriptedProvider(
        [
            # Contains the token BUT restates the raw provider name -> raw-leak violation.
            _narrative_json(f"At {token}, Dr. Alvarez examined the patient."),
            _narrative_json(f"At {token}, the patient was examined."),  # clean on regen
        ]
    )
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)

    assert outcome.narratives_generated == 1
    assert len(provider.calls) == 2
    # The named violation is about the provider name.
    assert "provider name" in provider.calls[1][2]
    db.refresh(enc)
    assert "Alvarez" not in enc.narrative_tokenized


def test_raw_provider_name_leak_twice_fails(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. Alvarez",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)
    token = _fact_token_for(db, matter, enc)

    provider = ScriptedProvider(
        [
            _narrative_json(f"At {token}, Dr. Alvarez examined the patient."),
            _narrative_json(f"At {token}, Dr. Alvarez saw the patient again."),
        ]
    )
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)
    assert outcome.narratives_failed == 1
    db.refresh(enc)
    assert enc.narrative_tokenized == ""


# --------------------------------------------------------------------------------------
# Unregistered token — gate AND build scan
# --------------------------------------------------------------------------------------


def test_unregistered_token_in_generation_is_rejected_by_gate(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)
    token = _fact_token_for(db, matter, enc)

    provider = ScriptedProvider(
        [
            # Own token present, but also cites a slot the registry never minted.
            _narrative_json(f"At {token}, see also [[FACT_99]]."),
            _narrative_json(f"At {token}, the patient reported neck pain."),
        ]
    )
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)

    assert outcome.narratives_generated == 1
    assert "do not resolve" in provider.calls[1][2]  # violation names the unresolved token
    db.refresh(enc)
    assert "[[FACT_99]]" not in enc.narrative_tokenized


def test_build_scan_reports_unregistered_token_forced_into_column(
    db: Session, matter: Matter
) -> None:
    doc = _make_document(db, matter)
    # A narrative pre-written directly onto the column, bypassing the generation gate, carrying an
    # orphan token: the build's zero-unregistered-claims scan must report it.
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
        narrative="At [[FACT_1]], see also [[FACT_99]].",
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)

    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    assert outcome.unregistered_claims == ("[[FACT_99]]",)
    assert len(outcome.rows) == 1  # build does not raise — it returns the finding


# --------------------------------------------------------------------------------------
# client None / provider unavailable
# --------------------------------------------------------------------------------------


def test_client_none_builds_rows_without_narratives(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)

    outcome = build_chronology(db, None, matter=matter)  # generate_narratives defaults True
    assert len(outcome.rows) == 1
    assert outcome.narratives_generated == 0
    assert outcome.narratives_skipped == 0  # client is None -> generation not attempted at all


def test_provider_unavailable_skips_rest_and_builds(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    for i, prov in enumerate(("Dr. A", "Dr. B")):
        _make_encounter(
            db,
            matter,
            provider=prov,
            encounter_type="ER",
            dos=dt.date(2026, 1, 10 + i),
            anchors=[_anchor(doc.id, i + 1)],
            created_at=dt.datetime(2026, 2, 1 + i, tzinfo=dt.UTC),
        )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)

    # Provider raises on the FIRST call -> the whole run stops attempting, both encounters skipped.
    provider = ScriptedProvider([ProviderNotConfigured("no live provider")])
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)

    assert outcome.narratives_generated == 0
    assert outcome.narratives_skipped == 2
    assert len(provider.calls) == 1  # stopped after the first failure
    assert len(outcome.rows) == 2
    # The metered client still ledgered the failed attempt (zero cost).
    assert len(_llm_calls(db, matter)) == 1


def test_missing_fact_token_is_skipped_not_minted(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    # NOTE: sync_extracted_facts intentionally NOT run -> no FACT token for the encounter.
    provider = ScriptedProvider([_narrative_json("At [[FACT_1]], neck pain.")])
    client = _metered(db, matter, provider)
    outcome = build_chronology(db, client, matter=matter)

    assert outcome.narratives_generated == 0
    assert outcome.narratives_skipped == 1
    assert provider.calls == []  # never called the model
    tokens = db.execute(select(FactToken).where(FactToken.matter_id == matter.id)).scalars().all()
    assert tokens == []


# --------------------------------------------------------------------------------------
# Overlays — apply / conflict / orphan + audit
# --------------------------------------------------------------------------------------


def test_upsert_overlay_applies_and_audits(db: Session, matter: Matter, user: User) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()

    overlay = upsert_overlay(
        db,
        user=user,
        matter=matter,
        encounter=enc,
        edited_fields={"work_status": "off work 2 weeks"},
    )
    assert overlay.status == OverlayStatus.APPLIED.value
    assert overlay.base_hash_at_edit == base_hash_for(enc)
    assert overlay.actor_id == user.id

    # Audit event written.
    events = list(
        db.execute(
            select(AuditEvent).where(AuditEvent.event_kind == "chronology_overlay_upserted")
        ).scalars()
    )
    assert len(events) == 1
    assert events[0].payload["encounter_id"] == str(enc.id)
    assert events[0].payload["created"] is True

    # The build lays the edit over the base in effective_fields.
    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    row = outcome.rows[0]
    assert row.overlay_status == OverlayStatus.APPLIED.value
    assert row.effective_fields["work_status"] == "off work 2 weeks"
    assert outcome.overlays_applied == 1


def test_overlay_conflicts_when_base_drifts(db: Session, matter: Matter, user: User) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
        diagnoses=["whiplash"],
    )
    db.commit()
    upsert_overlay(
        db,
        user=user,
        matter=matter,
        encounter=enc,
        edited_fields={"work_status": "off work 2 weeks"},
    )

    # Mutate the encounter's base AFTER the edit -> the overlay's base_hash_at_edit no longer
    # matches -> CONFLICT.
    enc.diagnoses = ["whiplash", "concussion"]
    db.add(enc)
    db.commit()

    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    row = outcome.rows[0]
    assert row.overlay_status == OverlayStatus.CONFLICT.value
    assert outcome.overlays_conflict == 1
    # Base wins in effective_fields; the edit is NOT laid over.
    assert row.effective_fields["diagnoses"] == ["whiplash", "concussion"]
    assert "work_status" not in row.effective_fields or row.effective_fields["work_status"] is None

    # The overlay row is preserved (its edits still visible for G2a), status persisted CONFLICT.
    overlay = db.execute(
        select(ChronologyRowOverlay).where(ChronologyRowOverlay.encounter_id == enc.id)
    ).scalar_one()
    assert overlay.status == OverlayStatus.CONFLICT.value
    assert overlay.edited_fields == {"work_status": "off work 2 weeks"}


def test_overlay_parks_orphaned_when_encounter_absorbed(
    db: Session, matter: Matter, user: User
) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    upsert_overlay(
        db,
        user=user,
        matter=matter,
        encounter=enc,
        edited_fields={"work_status": "off work 2 weeks"},
    )
    enc_id = enc.id

    # Simulate a merge absorbing the encounter (its row is gone).
    db.delete(enc)
    db.commit()

    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    assert outcome.rows == ()  # no encounter -> no row
    assert outcome.overlays_parked == 1

    overlay = db.execute(
        select(ChronologyRowOverlay).where(ChronologyRowOverlay.encounter_id == enc_id)
    ).scalar_one()
    assert overlay.status == OverlayStatus.PARKED_ORPHANED.value  # parked, never deleted
    assert overlay.edited_fields == {"work_status": "off work 2 weeks"}


def test_upsert_overlay_replaces_edited_fields_wholesale(
    db: Session, matter: Matter, user: User
) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    upsert_overlay(db, user=user, matter=matter, encounter=enc, edited_fields={"facility": "St. X"})
    upsert_overlay(
        db,
        user=user,
        matter=matter,
        encounter=enc,
        edited_fields={"work_status": "light duty"},
    )

    overlays = list(
        db.execute(
            select(ChronologyRowOverlay).where(ChronologyRowOverlay.encounter_id == enc.id)
        ).scalars()
    )
    assert len(overlays) == 1  # single (matter, encounter) row, updated in place
    assert overlays[0].edited_fields == {"work_status": "light duty"}  # replaced wholesale


# --------------------------------------------------------------------------------------
# render_rows_for_wire
# --------------------------------------------------------------------------------------


def test_render_rows_for_wire_detokenizes_and_stays_clean(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    enc = _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
        narrative="At [[FACT_1]], the patient reported neck pain.",
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)

    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    wire = render_rows_for_wire(db, matter=matter, rows=outcome.rows)

    assert len(wire) == 1
    row = wire[0]
    assert row["row_id"] == str(enc.id)
    assert row["date_of_service"] == "2026-01-10"
    assert row["provider_display"] == "Dr. A"
    # Token resolved to its display form; NOTHING token-shaped survives.
    assert "the ER visit to Dr. A on 2026-01-10" in row["narrative"]
    assert TOKEN_RE.search(row["narrative"]) is None


def test_render_rows_for_wire_orphan_becomes_sentinel(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
        narrative="See [[FACT_1]] and also [[FACT_99]] (orphan).",
    )
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)

    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    wire = render_rows_for_wire(db, matter=matter, rows=outcome.rows)

    narrative = wire[0]["narrative"]
    assert SENTINEL in narrative  # orphan rendered as sentinel
    assert TOKEN_RE.search(narrative) is None  # inv 11: wire clean


def test_render_empty_narrative_is_empty_string(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    db.commit()
    outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    wire = render_rows_for_wire(db, matter=matter, rows=outcome.rows)
    assert wire[0]["narrative"] == ""


# --------------------------------------------------------------------------------------
# Public-name / stage-id guards (the evals wave is specced against these)
# --------------------------------------------------------------------------------------


def test_public_surface_names_are_stable() -> None:
    assert chronology._NARRATIVE_STAGE == "chronology.narrative"
    assert chronology._OVERLAY_AUDIT_KIND == "chronology_overlay_upserted"
    assert ChronologyRow.__dataclass_fields__.keys() == {
        "row_id",
        "date_of_service",
        "provider_display",
        "facility_display",
        "encounter_type",
        "narrative_tokenized",
        "anchors",
        "base_hash",
        "overlay_status",
        "effective_fields",
    }


# --------------------------------------------------------------------------------------
# hub-check green with the new contract row
# --------------------------------------------------------------------------------------


def test_hub_check_passes_with_new_contract_row() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "hub_check.py"
    result = subprocess.run(
        ["python3", str(script)], capture_output=True, text=True, cwd=str(repo_root)
    )
    assert result.returncode == 0, result.stderr
    assert "hub-check: OK" in result.stdout
