"""Demand-generation run tests (M5 Wave B1) — the ``drafting -> compliance_review`` SSE run.

Self-contained in-memory engine + firm/user/matter parked at ``drafting``, tokens minted via the
real registry, and an APPROVED :class:`~app.models.orm.StrategyPlan` with a controlled two-section
skeleton so scripted bodies validate deterministically. A
:class:`~app.core.llm_provider.ScriptedProvider` drives the memo + per-section drafter calls behind
a real :class:`~app.core.llm_telemetry.MeteredLLMClient`, so metering runs end to end. Synthetic
data only — no PHI.

Coverage: the happy run's FRAME ORDER (started -> memo step -> section frames -> gate_ready ->
completed) + the draft VALIDATED + the gate advance + the ``draft_completed`` audit; a section that
fails twice -> an ERROR frame with violations, the draft stays DRAFTING, no advance, remaining
sections still drafted; the registry-drift refusal; the unapproved-plan refusal; and the
``post_draft`` hook firing between validation and advance.
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
from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.matter_logs import MatterRunLogger
from app.engine.brain2.generate import run_demand_generation
from app.engine.tokenizer import registry
from app.models.enums import DraftStatus, GateState, SectionValidation, SseEvent
from app.models.orm import (
    AuditEvent,
    DemandDraft,
    DraftSection,
    Firm,
    Matter,
    StrategyPlan,
    User,
)
from app.models.schemas import PlannedSection

# --------------------------------------------------------------------------------------
# Fixtures
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
        gate_state=GateState.DRAFTING.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()
    return m


def _mint_fact(db: Session, matter: Matter, user: User, display: str) -> str:
    row = registry.mint_attorney_fact(
        db, matter=matter, user=user, display_form=display, value={"note": display}
    )
    db.refresh(matter)
    return row.token_id


def _approved_plan(db: Session, matter: Matter, sections: list[PlannedSection]) -> StrategyPlan:
    plan = StrategyPlan(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        demand_amount_cents=None,
        demand_type="open",
        sections=[s.model_dump() for s in sections],
        emphasis_directives=[],
        approved=True,
    )
    db.add(plan)
    db.commit()
    return plan


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=50, output_tokens=30, cost_cents=1)


def _memo(body: str = "The strategy is straightforward.") -> CompletionResult:
    return _completion(json.dumps({"memo": body}))


def _section(body: str) -> CompletionResult:
    return _completion(json.dumps({"body_tokenized": body}))


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


def _two_section_plan(db: Session, matter: Matter, user: User) -> tuple[StrategyPlan, str, str]:
    """An approved 2-section plan: liability (requires FACT_a) + intro (no tokens). Returns ids."""
    fact_a = _mint_fact(db, matter, user, "the initial visit")
    fact_b = _mint_fact(db, matter, user, "the follow-up visit")
    liability = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact_a, fact_b],
        required_tokens=[fact_a],
        max_words=100,
    )
    intro = PlannedSection(
        section_id="intro_and_representation",
        purpose="Introduce representation.",
        allowed_tokens=[],
        required_tokens=[],
        max_words=100,
    )
    plan = _approved_plan(db, matter, [liability, intro])
    return plan, fact_a, fact_b


# --------------------------------------------------------------------------------------
# Happy path — frame order, VALIDATED, gate advance, audit
# --------------------------------------------------------------------------------------


def test_happy_path_frame_order_advances(db: Session, matter: Matter, user: User, tmp_path) -> None:
    plan, fact_a, _ = _two_section_plan(db, matter, user)
    logger = MatterRunLogger(matter.id, "demand", logs_dir=tmp_path)
    provider = ScriptedProvider(
        [
            _memo(),
            _section(f"Fault is clear from [[{fact_a}]]."),  # liability — passes
            _section("We represent the claimant and present this demand."),  # intro — passes
        ]
    )
    frames = list(
        run_demand_generation(db, matter=matter, user=user, provider=provider, run_logger=logger)
    )
    parsed = _parse_frames(frames)
    names = [name for name, _ in parsed]

    # started -> memo step -> section (liability) -> section (intro) -> gate_ready -> completed.
    assert names == [
        SseEvent.STATUS.value,  # started
        SseEvent.STATUS.value,  # step: memo
        SseEvent.SECTION.value,  # liability rendered
        SseEvent.SECTION.value,  # intro rendered
        SseEvent.GATE_READY.value,
        SseEvent.STATUS.value,  # completed
    ]
    assert parsed[1][1]["step"] == "memo"
    # Section frames carry the RENDERED preview (never the tokenized body).
    liability_frame = parsed[2][1]
    assert liability_frame["section_id"] == "liability"
    assert liability_frame["rendered_preview"] == "Fault is clear from the initial visit."
    assert "[[" not in liability_frame["rendered_preview"]
    assert parsed[3][1]["section_id"] == "intro_and_representation"
    # gate_ready -> compliance_review.
    assert parsed[4][1] == {"gate": "compliance_review", "matter_id": str(matter.id)}
    # completed summary.
    completed = parsed[5][1]
    assert completed["state"] == "completed"
    assert completed["sections_total"] == 2
    assert completed["sections_passed"] == 2
    assert completed["sections_failed"] == 0
    assert completed["gate_advanced"] is True

    # Matter advanced; draft VALIDATED; memo stored.
    db.refresh(matter)
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value
    draft = db.execute(select(DemandDraft).where(DemandDraft.matter_id == matter.id)).scalar_one()
    assert draft.status == DraftStatus.VALIDATED.value
    assert draft.memo == "The strategy is straightforward."
    # Both sections PASSED + rendered.
    sections = list(
        db.execute(select(DraftSection).where(DraftSection.draft_id == draft.id)).scalars()
    )
    assert {s.validation for s in sections} == {SectionValidation.PASSED.value}
    assert all(s.rendered_preview for s in sections)

    # draft_completed audit fired.
    kinds = [
        e.event_kind
        for e in db.execute(
            select(AuditEvent).where(AuditEvent.firm_id == matter.firm_id)
        ).scalars()
    ]
    assert "draft_completed" in kinds

    events = _log_events(logger)
    assert "run_started" in events
    assert "gate_advanced" in events
    assert "run_completed" in events


# --------------------------------------------------------------------------------------
# A section failing twice — ERROR frame, draft stays DRAFTING, no advance, others drafted
# --------------------------------------------------------------------------------------


def test_section_failing_twice_surfaces_and_blocks_advance(
    db: Session, matter: Matter, user: User, tmp_path
) -> None:
    plan, fact_a, _ = _two_section_plan(db, matter, user)
    logger = MatterRunLogger(matter.id, "demand", logs_dir=tmp_path)
    # liability: BOTH attempts omit the required token -> fails twice -> SURFACED_FAILED.
    # intro: passes. The run CONTINUES past the failure so intro still drafts.
    provider = ScriptedProvider(
        [
            _memo(),
            _section("Fault is clear but the required token is missing."),  # liability attempt 1
            _section("Still missing the required token entirely."),  # liability retry (attempt 2)
            _section("We represent the claimant and present this demand."),  # intro — passes
        ]
    )
    frames = list(
        run_demand_generation(db, matter=matter, user=user, provider=provider, run_logger=logger)
    )
    parsed = _parse_frames(frames)
    names = [name for name, _ in parsed]

    # An ERROR frame carries the section + violations; the run continues to intro's SECTION frame.
    error_frames = [(n, d) for n, d in parsed if n == SseEvent.ERROR.value]
    assert len(error_frames) == 1
    err = error_frames[0][1]
    assert err["error"] == "section_validation_failed"
    assert err["section_id"] == "liability"
    assert any("missing the required token" in v for v in err["violations"])
    # intro still drafted (a SECTION frame after the ERROR).
    section_frames = [d for n, d in parsed if n == SseEvent.SECTION.value]
    assert [d["section_id"] for d in section_frames] == ["intro_and_representation"]
    # NO gate_ready; a draft_incomplete STATUS instead.
    assert SseEvent.GATE_READY.value not in names
    incomplete = [
        d for n, d in parsed if n == SseEvent.STATUS.value and d.get("state") == "draft_incomplete"
    ]
    assert incomplete and incomplete[0]["failed_sections"] == ["liability"]
    # completed summary reflects the failure.
    completed = [
        d for n, d in parsed if n == SseEvent.STATUS.value and d.get("state") == "completed"
    ][0]
    assert completed["sections_failed"] == 1
    assert completed["sections_passed"] == 1
    assert completed["gate_advanced"] is False

    # Matter did NOT advance; draft stays DRAFTING.
    db.refresh(matter)
    assert matter.gate_state == GateState.DRAFTING.value
    draft = db.execute(select(DemandDraft).where(DemandDraft.matter_id == matter.id)).scalar_one()
    assert draft.status == DraftStatus.DRAFTING.value
    sections = {
        s.section_id: s.validation
        for s in db.execute(select(DraftSection).where(DraftSection.draft_id == draft.id)).scalars()
    }
    assert sections["liability"] == SectionValidation.SURFACED_FAILED.value
    assert sections["intro_and_representation"] == SectionValidation.PASSED.value
    # Exactly one DraftSection row per section (the retry folded onto the slot, no orphan row).
    assert len(sections) == 2


# --------------------------------------------------------------------------------------
# Refusals — registry drift, unapproved plan, wrong gate state
# --------------------------------------------------------------------------------------


def test_registry_drift_refusal(db: Session, matter: Matter, user: User) -> None:
    # Plan minted at registry_version 0; then the registry bumps (mint another fact) -> drift.
    plan, _, _ = _two_section_plan(db, matter, user)
    _mint_fact(db, matter, user, "a later fact")  # bumps matter.registry_version past the plan's
    db.refresh(matter)
    assert matter.registry_version != plan.registry_version

    provider = ScriptedProvider([])
    frames = list(run_demand_generation(db, matter=matter, user=user, provider=provider))
    parsed = _parse_frames(frames)
    errors = [d for n, d in parsed if n == SseEvent.ERROR.value]
    assert errors and errors[0]["error"] == "registry_drift"
    # No draft created; no advance; the provider was never called.
    db.refresh(matter)
    assert matter.gate_state == GateState.DRAFTING.value
    assert provider.calls == []
    assert db.execute(select(DemandDraft).where(DemandDraft.matter_id == matter.id)).first() is None


def test_unapproved_plan_refusal(db: Session, matter: Matter, user: User) -> None:
    # A plan exists but is NOT approved -> no_approved_plan refusal.
    fact = _mint_fact(db, matter, user, "the visit")
    liability = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    plan = _approved_plan(db, matter, [liability])
    plan.approved = False
    db.add(plan)
    db.commit()

    provider = ScriptedProvider([])
    frames = list(run_demand_generation(db, matter=matter, user=user, provider=provider))
    parsed = _parse_frames(frames)
    errors = [d for n, d in parsed if n == SseEvent.ERROR.value]
    assert errors and errors[0]["error"] == "no_approved_plan"
    assert provider.calls == []


def test_wrong_gate_state_refusal(db: Session, matter: Matter, user: User) -> None:
    _two_section_plan(db, matter, user)
    matter.gate_state = GateState.PLAN_REVIEW.value  # not drafting
    db.add(matter)
    db.commit()

    provider = ScriptedProvider([])
    frames = list(run_demand_generation(db, matter=matter, user=user, provider=provider))
    parsed = _parse_frames(frames)
    errors = [d for n, d in parsed if n == SseEvent.ERROR.value]
    assert errors and errors[0]["error"] == "wrong_gate_state"
    assert provider.calls == []


# --------------------------------------------------------------------------------------
# post_draft hook — fires between validation and advance
# --------------------------------------------------------------------------------------


def test_post_draft_hook_invoked_between_validation_and_advance(
    db: Session, matter: Matter, user: User
) -> None:
    plan, fact_a, _ = _two_section_plan(db, matter, user)
    captured: list[tuple[str, str]] = []

    def _hook(session: Session, m: Matter, draft: DemandDraft) -> None:
        # Called AFTER the draft is VALIDATED but BEFORE the gate advances.
        captured.append((draft.status, m.gate_state))

    provider = ScriptedProvider(
        [
            _memo(),
            _section(f"Fault is clear from [[{fact_a}]]."),
            _section("We represent the claimant and present this demand."),
        ]
    )
    list(run_demand_generation(db, matter=matter, user=user, provider=provider, post_draft=_hook))
    assert len(captured) == 1
    status_at_hook, gate_at_hook = captured[0]
    assert status_at_hook == DraftStatus.VALIDATED.value  # draft already validated
    assert gate_at_hook == GateState.DRAFTING.value  # gate not yet advanced
    # After the run the gate DID advance.
    db.refresh(matter)
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value
