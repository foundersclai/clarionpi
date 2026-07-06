"""Encounter merge tests: deterministic collapse, LLM tiebreak, reversibility, idempotency.

Deterministic exact-key merges run with no model; near-matches (same day, provider Jaccard ≥ 0.5,
not exact-key equal) go to the tiebreak model — and are LEFT UNMERGED when the model is absent /
unavailable / unparseable (never guessed in code). Rows are built directly via the ORM.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.corpus.extraction.merge import merge_encounters
from app.models.enums import MergeBasis
from app.models.orm import LlmCall, Matter, MedicalEncounter, User


def _client(db: Session, matter: Matter, provider: ScriptedProvider) -> MeteredLLMClient:
    return MeteredLLMClient(provider, db, matter.firm_id, matter.id)


def _result(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=10, output_tokens=5, cost_cents=1)


def _anchor(doc_id: uuid.UUID, page: int, window_id: str) -> dict:
    return {
        "document_id": str(doc_id),
        "page": page,
        "window_id": window_id,
        "bbox": None,
        "field": None,
    }


def _mk_encounter(
    db: Session,
    matter: Matter,
    *,
    provider: str,
    dos: dt.date,
    encounter_type: str = "office visit",
    complaints: list[str] | None = None,
    findings: list[str] | None = None,
    anchors: list[dict] | None = None,
    field_confidence: dict | None = None,
    created_at: dt.datetime | None = None,
) -> MedicalEncounter:
    """Insert one MedicalEncounter directly; optionally pin ``created_at`` to fix survivor order."""
    row = MedicalEncounter(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        date_of_service=dos,
        provider=provider,
        facility="",
        encounter_type=encounter_type,
        complaints=complaints or [],
        findings=findings or [],
        diagnoses=[],
        procedures=[],
        work_status=None,
        narrative_tokenized="",
        anchors=anchors or [],
        merged_from=[],
        field_confidence=field_confidence or {},
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.commit()
    return row


# --------------------------------------------------------------------------------------
# deterministic exact-key collapse
# --------------------------------------------------------------------------------------


def test_exact_key_duplicates_collapse_into_earliest_survivor(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = uuid.uuid4()
    dos = dt.date(2026, 2, 1)
    early = dt.datetime(2026, 3, 1, 9, 0, 0, tzinfo=dt.UTC)
    late = dt.datetime(2026, 3, 1, 10, 0, 0, tzinfo=dt.UTC)
    # Same (provider, date, type) up to casefold + whitespace collapse → one group.
    survivor = _mk_encounter(
        db,
        matter,
        provider="Dr.  Smith",
        dos=dos,
        complaints=["neck pain"],
        findings=["tenderness"],
        anchors=[_anchor(doc, 1, f"{doc}:1-8")],
        field_confidence={"provider": 0.7},
        created_at=early,
    )
    absorbed = _mk_encounter(
        db,
        matter,
        provider="dr. smith",
        dos=dos,
        encounter_type="Office Visit",
        complaints=["back pain", "neck pain"],
        findings=["swelling"],
        anchors=[_anchor(doc, 2, f"{doc}:1-8")],
        field_confidence={"provider": 0.9},
        created_at=late,
    )

    outcome = merge_encounters(db, None, matter=matter)

    assert outcome.merged_groups == 1
    assert outcome.encounters_remaining == 1
    assert outcome.llm_tiebreaks == 0
    assert outcome.tiebreaks_skipped == 0

    remaining = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).all()
    assert len(remaining) == 1
    row = remaining[0]
    assert row.id == survivor.id  # earliest created_at is the survivor
    # Lists unioned, order-preserving deduped.
    assert row.complaints == ["neck pain", "back pain"]
    assert row.findings == ["tenderness", "swelling"]
    # Anchors deduped by (document_id, page, window_id) — both pages present.
    assert {a["page"] for a in row.anchors} == {1, 2}
    # field_confidence per-field max.
    assert row.field_confidence["provider"] == 0.9
    assert row.merge_basis == MergeBasis.DETERMINISTIC_KEY.value
    # Absorbed row gone.
    assert db.get(MedicalEncounter, absorbed.id) is None


def test_merged_from_carries_full_reversible_snapshot(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = uuid.uuid4()
    dos = dt.date(2026, 2, 1)
    # The survivor row (earliest created_at) — created but not referenced by id below.
    _mk_encounter(
        db,
        matter,
        provider="Dr. Smith",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    )
    absorbed = _mk_encounter(
        db,
        matter,
        provider="Dr. Smith",
        dos=dos,
        complaints=["neck pain"],
        anchors=[_anchor(doc, 2, f"{doc}:1-8")],
        created_at=dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC),
    )
    absorbed_id = absorbed.id

    merge_encounters(db, None, matter=matter)

    row = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).one()
    assert len(row.merged_from) == 1
    entry = row.merged_from[0]
    assert entry["encounter_id"] == str(absorbed_id)
    snap = entry["snapshot"]
    # The snapshot carries the absorbed row's full business state (JSON-safe) — reversible.
    assert snap["provider"] == "Dr. Smith"
    assert snap["date_of_service"] == dos.isoformat()
    assert snap["complaints"] == ["neck pain"]
    assert snap["anchors"] == [_anchor(doc, 2, f"{doc}:1-8")]


def test_three_exact_duplicates_collapse_to_one_group(
    db: Session, dev_user: User, matter: Matter
) -> None:
    dos = dt.date(2026, 2, 1)
    for i in range(3):
        _mk_encounter(
            db,
            matter,
            provider="Dr. Smith",
            dos=dos,
            complaints=[f"c{i}"],
            created_at=dt.datetime(2026, 3, 1, 9 + i, tzinfo=dt.UTC),
        )
    outcome = merge_encounters(db, None, matter=matter)
    assert outcome.merged_groups == 1  # one survivor absorbed two others
    assert outcome.encounters_remaining == 1
    row = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).one()
    assert row.complaints == ["c0", "c1", "c2"]
    assert len(row.merged_from) == 2


# --------------------------------------------------------------------------------------
# LLM tiebreak (near-matches only)
# --------------------------------------------------------------------------------------


def test_near_match_same_day_merged_on_llm_true(
    db: Session, dev_user: User, matter: Matter
) -> None:
    dos = dt.date(2026, 2, 1)
    # "Dr. J. Alvarez" vs "Alvarez, J." — token sets {dr, j, alvarez} vs {alvarez, j}: Jaccard
    # 2/3 ≥ 0.5, not exact-key equal → a genuine tiebreak.
    left = _mk_encounter(
        db,
        matter,
        provider="Dr. J. Alvarez",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    )
    right = _mk_encounter(
        db,
        matter,
        provider="Alvarez, J.",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC),
    )
    provider = ScriptedProvider([_result('{"same_visit": true}')])
    outcome = merge_encounters(db, _client(db, matter, provider), matter=matter)

    assert outcome.llm_tiebreaks == 1
    assert outcome.merged_groups == 1
    assert outcome.encounters_remaining == 1
    assert outcome.tiebreaks_skipped == 0
    row = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).one()
    assert row.id == left.id
    assert row.merge_basis == MergeBasis.LLM_TIEBREAK.value
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == "extract.merge"  # correct stage id
    assert db.get(MedicalEncounter, right.id) is None


def test_near_match_not_merged_on_llm_false(db: Session, dev_user: User, matter: Matter) -> None:
    dos = dt.date(2026, 2, 1)
    _mk_encounter(
        db,
        matter,
        provider="Dr. J. Alvarez",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    )
    _mk_encounter(
        db,
        matter,
        provider="Alvarez, J.",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC),
    )
    provider = ScriptedProvider([_result('{"same_visit": false}')])
    outcome = merge_encounters(db, _client(db, matter, provider), matter=matter)

    assert outcome.llm_tiebreaks == 0
    assert outcome.merged_groups == 0
    assert outcome.encounters_remaining == 2  # left unmerged
    assert outcome.tiebreaks_skipped == 1


def test_near_match_left_unmerged_when_client_is_none(
    db: Session, dev_user: User, matter: Matter
) -> None:
    dos = dt.date(2026, 2, 1)
    _mk_encounter(
        db,
        matter,
        provider="Dr. J. Alvarez",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    )
    _mk_encounter(
        db,
        matter,
        provider="Alvarez, J.",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC),
    )
    outcome = merge_encounters(db, None, matter=matter)  # no tiebreak model

    assert outcome.tiebreaks_skipped == 1
    assert outcome.llm_tiebreaks == 0
    assert outcome.encounters_remaining == 2


def test_tiebreak_unparseable_twice_leaves_unmerged_and_skipped(
    db: Session, dev_user: User, matter: Matter
) -> None:
    dos = dt.date(2026, 2, 1)
    _mk_encounter(
        db,
        matter,
        provider="Dr. J. Alvarez",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    )
    _mk_encounter(
        db,
        matter,
        provider="Alvarez, J.",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC),
    )
    # Both replies unparseable → double parse failure → skip (never guess).
    provider = ScriptedProvider([_result("no json"), _result("still no json")])
    outcome = merge_encounters(db, _client(db, matter, provider), matter=matter)

    assert outcome.tiebreaks_skipped == 1
    assert outcome.encounters_remaining == 2
    # Both attempts metered (a wasted attempt is still a real call).
    assert db.query(LlmCall).filter(LlmCall.matter_id == matter.id).count() == 2


def test_different_day_near_match_is_not_a_tiebreak(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # Same provider form but DIFFERENT dates → not a tiebreak candidate → no model call.
    _mk_encounter(
        db,
        matter,
        provider="Dr. J. Alvarez",
        dos=dt.date(2026, 2, 1),
        created_at=dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    )
    _mk_encounter(
        db,
        matter,
        provider="Alvarez, J.",
        dos=dt.date(2026, 2, 2),
        created_at=dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC),
    )
    provider = ScriptedProvider([])  # must not be called
    outcome = merge_encounters(db, _client(db, matter, provider), matter=matter)

    assert outcome.encounters_remaining == 2
    assert outcome.tiebreaks_skipped == 0
    assert provider.calls == []


# --------------------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------------------


def test_rerun_on_merged_data_finds_no_groups(db: Session, dev_user: User, matter: Matter) -> None:
    dos = dt.date(2026, 2, 1)
    _mk_encounter(
        db,
        matter,
        provider="Dr. Smith",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    )
    _mk_encounter(
        db,
        matter,
        provider="Dr. Smith",
        dos=dos,
        created_at=dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC),
    )
    first = merge_encounters(db, None, matter=matter)
    assert first.merged_groups == 1
    assert first.encounters_remaining == 1

    # Second pass on already-merged data: nothing left to collapse.
    second = merge_encounters(db, None, matter=matter)
    assert second.merged_groups == 0
    assert second.llm_tiebreaks == 0
    assert second.tiebreaks_skipped == 0
    assert second.encounters_remaining == 1


def test_no_duplicates_is_a_noop(db: Session, dev_user: User, matter: Matter) -> None:
    _mk_encounter(db, matter, provider="Dr. A", dos=dt.date(2026, 2, 1))
    _mk_encounter(db, matter, provider="Dr. B", dos=dt.date(2026, 2, 2))
    outcome = merge_encounters(db, None, matter=matter)
    assert outcome.merged_groups == 0
    assert outcome.encounters_remaining == 2
