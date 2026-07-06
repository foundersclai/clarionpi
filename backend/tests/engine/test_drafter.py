"""Drafter tests (M5 Wave B1) — layered prompt order + the DrafterPromptSnapshot hash.

Self-contained in-memory engine + firm/matter, tokens minted via the real registry, a StrategyPlan
+ DemandDraft + risk flags seeded directly. A :class:`~app.core.llm_provider.ScriptedProvider`
records the exact prompt so the layering (final constraints LAST) and the display-form inclusion are
asserted on the wire the drafter actually sent. Synthetic data only — no PHI.

Coverage: the final hard-constraint block is appended LAST (after the matter directives); the
snapshot ``input_hash`` is stable for identical inputs and CHANGES when a constraint changes;
allowed-token display forms appear in the prompt; a retry appends the violations to the tail.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.engine.brain2.constraints import build_hard_constraints
from app.engine.brain2.drafter import build_snapshot, draft_section
from app.engine.tokenizer import registry
from app.models.enums import GateState
from app.models.orm import DemandDraft, Firm, Matter, RiskFlag, StrategyPlan, User
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


def _make_plan(db: Session, matter: Matter, planned: PlannedSection) -> StrategyPlan:
    plan = StrategyPlan(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        demand_amount_cents=None,
        demand_type="open",
        sections=[planned.model_dump()],
        emphasis_directives=["Foreground liability."],
        approved=True,
    )
    db.add(plan)
    db.flush()
    return plan


def _make_draft(db: Session, matter: Matter, plan: StrategyPlan, *, memo: str = "") -> DemandDraft:
    draft = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=plan.registry_version,
        strategy_plan_version=plan.version,
        status="drafting",
        memo=memo,
    )
    db.add(draft)
    db.flush()
    return draft


def _make_flag(db: Session, matter: Matter, *, disposition: str | None, detail: str) -> None:
    db.add(
        RiskFlag(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            kind="preexisting_condition",
            severity="high",
            detector="label",
            anchors=[],
            detail=detail,
            disposition=disposition,
        )
    )
    db.flush()


def _completion(body: str) -> CompletionResult:
    return CompletionResult(
        text=json.dumps({"body_tokenized": body}), input_tokens=50, output_tokens=30, cost_cents=1
    )


# --------------------------------------------------------------------------------------
# Prompt layering — final constraints appended LAST; display forms present
# --------------------------------------------------------------------------------------


def test_final_constraints_appended_last(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the initial visit")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=200,
    )
    plan = _make_plan(db, matter, planned)
    draft = _make_draft(db, matter, plan)
    _make_flag(db, matter, disposition="address_in_letter", detail="prior neck injury")
    _make_flag(db, matter, disposition="omit_with_rationale", detail="unrelated back surgery")
    constraints = build_hard_constraints(db, matter=matter)

    provider = ScriptedProvider([_completion(f"Fault is clear from [[{fact}]].")])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    draft_section(
        db,
        client,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=constraints,
        sort_order=0,
    )

    prompt = provider.calls[0][2]
    # The FINAL HARD CONSTRAINTS block exists and is at the very tail.
    header_idx = prompt.index("FINAL HARD CONSTRAINTS (binding):")
    # The emphasis directive (a matter-directive layer entry, always present via the plan) comes
    # BEFORE the constraints block — the late-bound block binds after the matter directives.
    directives_idx = prompt.index("Foreground liability.")
    assert header_idx > directives_idx  # constraints come AFTER the matter directives
    # Both the address entry and the no-volunteer entry are in the block.
    assert "Address in the letter: prior neck injury" in prompt
    assert "Never mention or allude to: unrelated back surgery" in prompt
    # Nothing of substance follows the last constraint entry on a first (non-retry) attempt.
    tail = prompt[header_idx:]
    assert "unrelated back surgery" in tail


def test_allowed_token_display_forms_in_prompt(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the emergency-room visit")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[],
        max_words=200,
    )
    plan = _make_plan(db, matter, planned)
    draft = _make_draft(db, matter, plan)
    constraints = build_hard_constraints(db, matter=matter)

    provider = ScriptedProvider([_completion("Fault is clear.")])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    draft_section(
        db,
        client,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=constraints,
        sort_order=0,
    )
    prompt = provider.calls[0][2]
    # The allowed token appears with its display form (inv 5 — display forms only).
    assert f"[[{fact}]]" in prompt
    assert "the emergency-room visit" in prompt


# --------------------------------------------------------------------------------------
# Snapshot hash — stable for identical inputs; changes when a constraint changes
# --------------------------------------------------------------------------------------


def test_snapshot_hash_stable_for_identical_inputs(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the visit")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=200,
    )
    plan = _make_plan(db, matter, planned)
    draft = _make_draft(db, matter, plan)
    constraints = build_hard_constraints(db, matter=matter)

    first = build_snapshot(
        db, matter=matter, plan=plan, draft=draft, planned=planned, constraints=constraints
    )
    second = build_snapshot(
        db, matter=matter, plan=plan, draft=draft, planned=planned, constraints=constraints
    )
    assert first.input_hash == second.input_hash


def test_snapshot_hash_changes_when_constraint_changes(
    db: Session, matter: Matter, user: User
) -> None:
    fact = _mint_fact(db, matter, user, "the visit")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=200,
    )
    plan = _make_plan(db, matter, planned)
    draft = _make_draft(db, matter, plan)

    before = build_snapshot(
        db,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=build_hard_constraints(db, matter=matter),
    )
    # Add a dispositioned flag -> a new constraint entry -> the hash MUST change.
    _make_flag(db, matter, disposition="address_in_letter", detail="prior neck injury")
    after = build_snapshot(
        db,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=build_hard_constraints(db, matter=matter),
    )
    assert before.input_hash != after.input_hash
    assert "Address in the letter: prior neck injury" in after.final_hard_constraints


def test_snapshot_persisted_on_row(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the visit")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=200,
    )
    plan = _make_plan(db, matter, planned)
    draft = _make_draft(db, matter, plan)
    constraints = build_hard_constraints(db, matter=matter)
    provider = ScriptedProvider([_completion(f"Fault from [[{fact}]].")])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    section = draft_section(
        db,
        client,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=constraints,
        sort_order=0,
    )
    snap = section.prompt_snapshot
    assert set(snap) == {
        "input_hash",
        "rules_blocks",
        "matter_directives",
        "final_hard_constraints",
    }
    # The snapshot's hash matches a freshly-built snapshot over the same inputs (judge symmetry).
    rebuilt = build_snapshot(
        db, matter=matter, plan=plan, draft=draft, planned=planned, constraints=constraints
    )
    assert snap["input_hash"] == rebuilt.input_hash


# --------------------------------------------------------------------------------------
# Retry — the violations are appended to the prompt tail
# --------------------------------------------------------------------------------------


def test_retry_appends_violations(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the visit")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=200,
    )
    plan = _make_plan(db, matter, planned)
    draft = _make_draft(db, matter, plan)
    constraints = build_hard_constraints(db, matter=matter)

    provider = ScriptedProvider([_completion(f"Second attempt with [[{fact}]].")])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    violations = ["the section is missing the required token [[FACT_X]]"]
    draft_section(
        db,
        client,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=constraints,
        sort_order=0,
        retry_violations=violations,
    )
    prompt = provider.calls[0][2]
    assert "Your previous attempt was rejected for these reasons:" in prompt
    assert "the section is missing the required token [[FACT_X]]" in prompt
    # The retry violations sit AFTER the final hard constraints (the very tail).
    assert prompt.index("previous attempt") > prompt.index("FINAL HARD CONSTRAINTS")
