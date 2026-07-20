"""Fact-registry tests — token grammar, sync/idempotency/supersession, resolution, orphans.

Self-contained: builds its own in-memory SQLite engine, firm/user/matter, and encounter /
incident rows via direct ORM (the same shape as ``tests/core/conftest.py``), so this suite is
independent of the corpus/api conftests.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.engine.tokenizer import registry
from app.engine.tokenizer.registry import (
    SENTINEL,
    TOKEN_RE,
    parse_token,
    token_str,
)
from app.models.enums import (
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
    TokenKind,
    TokenSource,
    TokenStatus,
    UserRole,
)
from app.models.orm import (
    CaseDocument,
    DedupDecision,
    FactToken,
    Firm,
    IncidentFacts,
    Matter,
    MedicalEncounter,
    RegistryVersion,
    User,
)
from app.models.schemas import AmountFact

# --------------------------------------------------------------------------------------
# Fixtures — in-memory engine + firm/user/matter, direct ORM
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
def user(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="attorney@firm.test",
        display_name="Test Attorney",
        role=UserRole.ATTORNEY.value,
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
    """A minimal anchor target so an anchor's ``document_id`` resolves to a real doc."""
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
    created_at: dt.datetime | None = None,
) -> MedicalEncounter:
    enc = MedicalEncounter(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        date_of_service=dos,
        provider=provider,
        facility="General Hospital",
        encounter_type=encounter_type,
        complaints=["neck pain"],
        findings=[],
        diagnoses=diagnoses if diagnoses is not None else ["whiplash"],
        procedures=[],
        work_status=None,
        narrative_tokenized="",
        anchors=anchors,
        merged_from=[],
        field_confidence={},
    )
    if created_at is not None:
        enc.created_at = created_at
    db.add(enc)
    db.flush()
    return enc


def _make_incident(db: Session, matter: Matter, *, anchors: list[dict]) -> IncidentFacts:
    inc = IncidentFacts(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        payload={"location": "5th & Main", "narrative": "rear-ended at a light"},
        anchors=anchors,
    )
    db.add(inc)
    db.flush()
    return inc


def _fact_tokens(db: Session, matter: Matter) -> list[FactToken]:
    return list(
        db.execute(
            select(FactToken)
            .where(FactToken.matter_id == matter.id)
            .order_by(FactToken.registry_version, FactToken.token_id)
        ).scalars()
    )


# --------------------------------------------------------------------------------------
# Token grammar
# --------------------------------------------------------------------------------------


def test_token_str_parse_round_trip_all_kinds() -> None:
    cases = {
        TokenKind.FACT: "[[FACT_1]]",
        TokenKind.AMOUNT: "[[AMT_2]]",
        TokenKind.CITATION: "[[CITE_3]]",
        TokenKind.EXHIBIT: "[[EX_4]]",
    }
    for kind, expected in cases.items():
        ordinal = int(expected.rstrip("]").rsplit("_", 1)[1])
        rendered = token_str(kind, ordinal)
        assert rendered == expected
        assert parse_token(rendered) == (kind, ordinal)


def test_token_re_matches_all_four_prefixes() -> None:
    text = "a [[FACT_1]] b [[AMT_22]] c [[CITE_3]] d [[EX_400]] e"
    assert [m.group(0) for m in TOKEN_RE.finditer(text)] == [
        "[[FACT_1]]",
        "[[AMT_22]]",
        "[[CITE_3]]",
        "[[EX_400]]",
    ]


def test_sentinel_is_not_token_shaped() -> None:
    # The sentinel must never be re-parseable as a token — this is the inv-11 guard.
    assert TOKEN_RE.search(SENTINEL) is None
    assert TOKEN_RE.fullmatch(SENTINEL) is None


def test_parse_token_rejects_non_tokens() -> None:
    for bad in ("FACT_1", "[[FACT_1]", "[[foo_1]]", "[[FACT_x]]", "", SENTINEL):
        with pytest.raises(ValueError):
            parse_token(bad)


# --------------------------------------------------------------------------------------
# sync_extracted_facts — mint / idempotency / supersession
# --------------------------------------------------------------------------------------


def test_sync_mints_single_namespace_and_bumps(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    _make_encounter(
        db,
        matter,
        provider="Dr. B",
        encounter_type="follow-up",
        dos=dt.date(2026, 1, 20),
        anchors=[_anchor(doc.id, 2)],
        created_at=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
    )
    _make_incident(db, matter, anchors=[_anchor(doc.id, 3)])
    db.commit()

    outcome = registry.sync_extracted_facts(db, matter=matter)

    assert (outcome.minted, outcome.updated, outcome.unchanged) == (3, 0, 0)
    assert outcome.bumped is True
    assert outcome.version == 1
    assert matter.registry_version == 1

    rows = _fact_tokens(db, matter)
    assert [r.token_id for r in rows] == ["FACT_1", "FACT_2", "FACT_3"]
    assert all(r.registry_version == 1 for r in rows)
    assert all(r.status == TokenStatus.VERIFIED.value for r in rows)
    assert all(r.source == TokenSource.EXTRACTOR.value for r in rows)
    # created-order drives ordinal: Dr. A (created first) is FACT_1.
    by_id = {r.token_id: r for r in rows}
    assert "Dr. A" in by_id["FACT_1"].display_form
    assert "Dr. B" in by_id["FACT_2"].display_form
    assert by_id["FACT_3"].display_form == "the incident"
    # anchors copied verbatim.
    assert by_id["FACT_1"].anchors == [_anchor(doc.id, 1)]


def test_sync_is_idempotent(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    _make_encounter(
        db,
        matter,
        provider="Dr. B",
        encounter_type="PT",
        dos=dt.date(2026, 1, 20),
        anchors=[_anchor(doc.id, 2)],
    )
    _make_incident(db, matter, anchors=[_anchor(doc.id, 3)])
    db.commit()

    registry.sync_extracted_facts(db, matter=matter)
    second = registry.sync_extracted_facts(db, matter=matter)

    assert (second.minted, second.updated, second.unchanged) == (0, 0, 3)
    assert second.bumped is False
    assert second.version == 1
    assert matter.registry_version == 1
    # No new rows at a phantom v2.
    assert len(_fact_tokens(db, matter)) == 3


def test_sync_supersedes_on_content_change(db: Session, matter: Matter) -> None:
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
    registry.sync_extracted_facts(db, matter=matter)

    # Mutate the encounter's diagnoses -> content differs -> supersession.
    enc.diagnoses = ["whiplash", "concussion"]
    db.add(enc)
    db.commit()

    outcome = registry.sync_extracted_facts(db, matter=matter)
    assert (outcome.minted, outcome.updated, outcome.unchanged) == (0, 1, 0)
    assert outcome.bumped is True
    assert outcome.version == 2

    rows = [r for r in _fact_tokens(db, matter) if r.token_id == "FACT_1"]
    # Same token_id, two version rows — the old one intact, the new one at v2.
    assert {r.registry_version for r in rows} == {1, 2}
    v1 = next(r for r in rows if r.registry_version == 1)
    v2 = next(r for r in rows if r.registry_version == 2)
    assert isinstance(v1.value, dict) and isinstance(v2.value, dict)
    assert v1.value["diagnoses"] == ["whiplash"]
    assert v2.value["diagnoses"] == ["whiplash", "concussion"]


def test_sync_unverified_when_anchor_doc_superseded(
    db: Session, matter: Matter, user: User
) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    # A resolved dedup decision that supersedes the anchor's document.
    decision = DedupDecision(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        document_id=doc.id,
        against_document_id=None,
        status=DedupStatus.DUPLICATE_OF.value,
        page_hash_matches=[],
        resolution=DedupResolution.SUPERSEDED.value,
        resolved_by=user.id,
        resolved_at=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
    )
    db.add(decision)
    db.commit()

    registry.sync_extracted_facts(db, matter=matter)
    row = next(r for r in _fact_tokens(db, matter) if r.token_id == "FACT_1")
    assert row.status == TokenStatus.UNVERIFIED.value


def test_sync_deleted_source_marks_unverified_same_token(db: Session, matter: Matter) -> None:
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
    original_display = next(
        r for r in _fact_tokens(db, matter) if r.token_id == "FACT_1"
    ).display_form

    # Upstream deletes the encounter (a merge absorbed it).
    db.delete(enc)
    db.commit()

    outcome = registry.sync_extracted_facts(db, matter=matter)
    assert outcome.updated == 1
    assert outcome.bumped is True

    rows = [r for r in _fact_tokens(db, matter) if r.token_id == "FACT_1"]
    assert {r.registry_version for r in rows} == {1, 2}
    v2 = next(r for r in rows if r.registry_version == 2)
    # Fact-slot survives: same token_id, display_form unchanged, but status drops.
    assert v2.status == TokenStatus.UNVERIFIED.value
    assert v2.display_form == original_display


# --------------------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------------------


def test_resolve_for_prompt_returns_display_form(db: Session, matter: Matter) -> None:
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

    resolved = registry.resolve_for_prompt(db, matter=matter, token="[[FACT_1]]")
    assert resolved == "the ER visit to Dr. A on 2026-01-10"


def test_resolve_for_prompt_orphan_returns_sentinel_and_logs(
    db: Session, matter: Matter, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="clarionpi.registry"):
        resolved = registry.resolve_for_prompt(db, matter=matter, token="[[FACT_99]]")
    assert resolved == SENTINEL
    assert any(
        record.levelno == logging.ERROR and "[[FACT_99]]" in record.getMessage()
        for record in caplog.records
    )


# --------------------------------------------------------------------------------------
# gloss_tokens — batch prompt-mode display forms for the plan-review chips
# --------------------------------------------------------------------------------------


def test_gloss_tokens_resolves_fact_display_form(db: Session, matter: Matter) -> None:
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

    glosses = registry.gloss_tokens(db, matter=matter, token_ids=["FACT_1"])
    assert set(glosses) == {"FACT_1"}
    gloss = glosses["FACT_1"]
    assert gloss.resolved is True
    assert gloss.kind == "FACT"
    assert gloss.display_form == "the ER visit to Dr. A on 2026-01-10"
    assert gloss.hint is None  # hints are AMT-only


def test_gloss_tokens_amt_hint_disambiguates_ledger_slots(db: Session, matter: Matter) -> None:
    # Five near-identical "$X" display forms are useless on the G2.5 screen without knowing which
    # ledger slot each is — the hint labels them WITHOUT touching display_form (which the renderer
    # substitutes verbatim into letter prose and must stay a pure dollar string).
    amounts = [
        AmountFact(
            key="specials.grand.billed",
            value_cents=1_875_000,
            display_form="$18,750.00",
            ledger_ref={},
            ledger_hash="h1",
        ),
        AmountFact(
            key="specials.demand_basis",
            value_cents=1_875_000,
            display_form="$18,750.00",
            ledger_ref={},
            ledger_hash="h2",
        ),
        AmountFact(
            key="specials.category.er.billed",
            value_cents=150_000,
            display_form="$1,500.00",
            ledger_ref={},
            ledger_hash="h3",
        ),
        AmountFact(
            key="specials.category.pt_chiro.billed",
            value_cents=90_000,
            display_form="$900.00",
            ledger_ref={},
            ledger_hash="h4",
        ),
    ]
    registry.mint_amounts(db, matter=matter, amounts=amounts)

    glosses = registry.gloss_tokens(
        db, matter=matter, token_ids=["AMT_1", "AMT_2", "AMT_3", "AMT_4"]
    )
    assert glosses["AMT_1"].hint == "total billed specials"
    assert glosses["AMT_2"].hint == "demand basis"
    assert glosses["AMT_3"].hint == "ER billed"
    assert glosses["AMT_4"].hint == "PT / chiro billed"
    # display_form itself stays the prose-pure dollar string.
    assert glosses["AMT_1"].display_form == "$18,750.00"


def test_gloss_tokens_orphan_is_unresolved_sentinel(db: Session, matter: Matter) -> None:
    # A well-formed id with no row → SENTINEL + resolved=False (the FE flags it, never a raw leak).
    glosses = registry.gloss_tokens(db, matter=matter, token_ids=["FACT_99"])
    gloss = glosses["FACT_99"]
    assert gloss.resolved is False
    assert gloss.kind == "FACT"
    assert gloss.display_form == SENTINEL


def test_gloss_tokens_malformed_id_does_not_raise(db: Session, matter: Matter) -> None:
    # The required-token editor lets an attorney type free-form ids — a non-token must gloss as
    # unresolved (keyed as typed), not raise.
    glosses = registry.gloss_tokens(db, matter=matter, token_ids=["not-a-token"])
    gloss = glosses["not-a-token"]
    assert gloss.resolved is False
    assert gloss.kind == ""
    assert gloss.display_form == SENTINEL


def test_gloss_tokens_dedupes_bare_and_bracketed(db: Session, matter: Matter) -> None:
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

    # Bare id, bracketed form, a duplicate, and blanks all collapse to one normalized key.
    glosses = registry.gloss_tokens(
        db, matter=matter, token_ids=["FACT_1", "[[FACT_1]]", "FACT_1", "  "]
    )
    assert set(glosses) == {"FACT_1"}
    assert glosses["FACT_1"].display_form == "the ER visit to Dr. A on 2026-01-10"


def test_gloss_tokens_empty_input_is_empty(db: Session, matter: Matter) -> None:
    assert registry.gloss_tokens(db, matter=matter, token_ids=[]) == {}


def test_resolve_text_for_wire_substitutes_and_sentinels(db: Session, matter: Matter) -> None:
    doc = _make_document(db, matter)
    _make_encounter(
        db,
        matter,
        provider="Dr. A",
        encounter_type="ER",
        dos=dt.date(2026, 1, 10),
        anchors=[_anchor(doc.id, 1)],
    )
    _make_incident(db, matter, anchors=[_anchor(doc.id, 3)])
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)

    text = "Saw [[FACT_1]] after [[FACT_2]]; also [[FACT_99]] (bad)."
    out = registry.resolve_text_for_wire(db, matter=matter, text=text)

    assert "the ER visit to Dr. A on 2026-01-10" in out
    assert "the incident" in out
    assert SENTINEL in out
    # inv-11: nothing token-shaped survives.
    assert TOKEN_RE.search(out) is None


def test_resolve_text_for_wire_raises_on_poisoned_display_form(db: Session, matter: Matter) -> None:
    # A display_form that itself carries a token is a data bug — wire resolution must raise,
    # not leak it.
    registry.bump_version(db, matter=matter, reason="extraction_sync")
    poisoned = FactToken(
        matter_id=matter.id,
        token_id="FACT_1",
        registry_version=matter.registry_version,
        kind=TokenKind.FACT.value,
        value={},
        display_form="see [[FACT_1]] recursively",
        anchors=[],
        status=TokenStatus.VERIFIED.value,
        source=TokenSource.EXTRACTOR.value,
        source_ref="encounter:poison",
    )
    poisoned.firm_id = matter.firm_id
    db.add(poisoned)
    db.commit()

    with pytest.raises(ValueError, match="token survived wire resolution"):
        registry.resolve_text_for_wire(db, matter=matter, text="x [[FACT_1]] y")


def test_scan_unregistered_returns_exact_list(db: Session, matter: Matter) -> None:
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

    text = "[[FACT_1]] ok, [[FACT_50]] missing, [[AMT_7]] missing, [[FACT_50]] dup"
    assert registry.scan_unregistered(db, matter=matter, text=text) == ["[[FACT_50]]", "[[AMT_7]]"]


# --------------------------------------------------------------------------------------
# Attorney facts + version chain
# --------------------------------------------------------------------------------------


def test_mint_attorney_fact_verified_and_bumps(db: Session, matter: Matter, user: User) -> None:
    row = registry.mint_attorney_fact(
        db,
        matter=matter,
        user=user,
        display_form="the client's prior back surgery in 2019",
        value={"note": "prior surgery"},
    )
    assert row.source == TokenSource.ATTORNEY.value
    assert row.status == TokenStatus.VERIFIED.value
    assert row.token_id == "FACT_1"
    assert row.registry_version == 1
    assert matter.registry_version == 1
    assert row.source_ref is not None and row.source_ref.startswith("attorney:")


def test_mint_attorney_fact_continues_shared_ordinal(
    db: Session, matter: Matter, user: User
) -> None:
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
    registry.sync_extracted_facts(db, matter=matter)  # FACT_1

    row = registry.mint_attorney_fact(
        db, matter=matter, user=user, display_form="attorney note", value={}
    )
    # Shared namespace: the attorney fact takes the next ordinal after the extracted one.
    assert row.token_id == "FACT_2"


def test_bump_version_builds_parent_chain(db: Session, matter: Matter) -> None:
    v1 = registry.bump_version(db, matter=matter, reason="extraction_sync")
    v2 = registry.bump_version(db, matter=matter, reason="ledger_sync")
    v3 = registry.bump_version(db, matter=matter, reason="attorney_fact")
    db.commit()

    assert (v1, v2, v3) == (1, 2, 3)
    assert matter.registry_version == 3

    versions = list(
        db.execute(
            select(RegistryVersion)
            .where(RegistryVersion.matter_id == matter.id)
            .order_by(RegistryVersion.version)
        ).scalars()
    )
    assert [(v.version, v.parent_version, v.change_reason) for v in versions] == [
        (1, 0, "extraction_sync"),
        (2, 1, "ledger_sync"),
        (3, 2, "attorney_fact"),
    ]
    assert all(v.frozen is False for v in versions)


def test_current_version_mirrors_matter(db: Session, matter: Matter) -> None:
    assert registry.current_version(db, matter=matter) == 0
    registry.bump_version(db, matter=matter, reason="extraction_sync")
    db.commit()
    assert registry.current_version(db, matter=matter) == 1
