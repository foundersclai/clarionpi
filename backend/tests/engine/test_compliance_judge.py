"""Semantic G3 judge tests (M5 Wave C) — snapshot symmetry + fail-visible verdicts.

Self-contained in-memory engine + firm/matter parked at ``compliance_review``. A section is drafted
via the REAL :class:`~app.engine.brain2.drafter.draft_section` (so its persisted snapshot matches
what the judge re-derives), then a :class:`~app.core.llm_provider.ScriptedProvider` drives the judge
calls behind a real :class:`~app.core.llm_telemetry.MeteredLLMClient`. Synthetic data only — no PHI.

Coverage: a post-draft constraint mutation -> :class:`SnapshotDrift`; a scripted clean verdict ->
no findings; a scripted semantic finding -> persisted with a semantic ``check_kind``; a judge reply
that claims a MECHANICAL kind burns the retry then yields the manual-review ``tone`` finding; and
the per-section judge-call count is metered (one call per section).
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
from app.engine.brain2.drafter import draft_section
from app.engine.compliance.judge import SnapshotDrift, run_judge
from app.engine.tokenizer import registry
from app.models.enums import CheckKind, FindingBucket, GateState
from app.models.orm import DemandDraft, DraftSection, Firm, Matter, RiskFlag, StrategyPlan, User
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
        gate_state=GateState.COMPLIANCE_REVIEW.value,
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


def _plan(db: Session, matter: Matter, planned: PlannedSection) -> StrategyPlan:
    plan = StrategyPlan(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        demand_amount_cents=None,
        demand_type="open",
        sections=[planned.model_dump()],
        emphasis_directives=[],
        approved=True,
    )
    db.add(plan)
    db.flush()
    return plan


def _draft(db: Session, matter: Matter, plan: StrategyPlan) -> DemandDraft:
    draft = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=plan.registry_version,
        strategy_plan_version=plan.version,
        status="validated",
    )
    db.add(draft)
    db.flush()
    return draft


def _section_body(body: str) -> CompletionResult:
    return CompletionResult(
        text=json.dumps({"body_tokenized": body}), input_tokens=50, output_tokens=30, cost_cents=1
    )


def _judge_reply(findings: list[dict]) -> CompletionResult:
    return CompletionResult(
        text=json.dumps({"findings": findings}), input_tokens=40, output_tokens=20, cost_cents=1
    )


def _draft_one(
    db: Session,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    planned: PlannedSection,
    body: str,
) -> DraftSection:
    """Draft a section via the real drafter so its persisted snapshot is production-shaped."""
    provider = ScriptedProvider([_section_body(body)])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    constraints = build_hard_constraints(db, matter=matter)
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
    db.commit()
    return section


def _simple_section(
    db: Session, matter: Matter, user: User
) -> tuple[StrategyPlan, DemandDraft, DraftSection]:
    fact = _mint_fact(db, matter, user, "the initial visit")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    plan = _plan(db, matter, planned)
    draft = _draft(db, matter, plan)
    section = _draft_one(db, matter, plan, draft, planned, f"Fault is clear from [[{fact}]].")
    return plan, draft, section


# --------------------------------------------------------------------------------------
# Snapshot symmetry — a post-draft constraint mutation fails the pass
# --------------------------------------------------------------------------------------


def test_snapshot_drift_raises(db: Session, matter: Matter, user: User) -> None:
    plan, draft, section = _simple_section(db, matter, user)
    # Mutate a hard constraint AFTER the section was drafted: add a dispositioned risk flag, which
    # changes build_hard_constraints -> the rebuilt snapshot hash no longer matches the persisted.
    db.add(
        RiskFlag(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            kind="preexisting_condition",
            severity="high",
            detector="label",
            anchors=[],
            detail="prior neck injury",
            disposition="address_in_letter",
        )
    )
    db.commit()

    provider = ScriptedProvider([_judge_reply([])])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    with pytest.raises(SnapshotDrift) as excinfo:
        run_judge(db, client, matter=matter, plan=plan, draft=draft, sections=[section])
    assert excinfo.value.section_id == "liability"
    # The symmetry gate runs BEFORE any judge call — the provider was never called.
    assert provider.calls == []


# --------------------------------------------------------------------------------------
# Scripted clean + scripted semantic finding
# --------------------------------------------------------------------------------------


def test_clean_verdict_no_findings(db: Session, matter: Matter, user: User) -> None:
    plan, draft, section = _simple_section(db, matter, user)
    provider = ScriptedProvider([_judge_reply([])])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    findings = run_judge(db, client, matter=matter, plan=plan, draft=draft, sections=[section])
    assert findings == []
    assert len(provider.calls) == 1  # one judge call for the one section


def test_semantic_finding_returned(db: Session, matter: Matter, user: User) -> None:
    plan, draft, section = _simple_section(db, matter, user)
    provider = ScriptedProvider(
        [
            _judge_reply(
                [
                    {
                        "check_kind": CheckKind.STRATEGY_DRIFT.value,
                        "section_id": "liability",
                        "detail": "the section drifts from the stated liability theory",
                    }
                ]
            )
        ]
    )
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    findings = run_judge(db, client, matter=matter, plan=plan, draft=draft, sections=[section])
    assert len(findings) == 1
    assert findings[0].check_kind == CheckKind.STRATEGY_DRIFT.value
    assert findings[0].section_id == "liability"
    # A semantic kind buckets SEMANTIC (the engine applies this; assert the routing here too).
    from app.engine.compliance.engine import bucket_for

    assert bucket_for(CheckKind(findings[0].check_kind)) is FindingBucket.SEMANTIC


# --------------------------------------------------------------------------------------
# A mechanical-claiming reply burns the retry, then yields the manual-review TONE finding
# --------------------------------------------------------------------------------------


def test_mechanical_claim_burns_retry_then_manual_review(
    db: Session, matter: Matter, user: User
) -> None:
    plan, draft, section = _simple_section(db, matter, user)
    # BOTH judge replies claim a MECHANICAL kind -> JudgeFindingBatch validation rejects both.
    mechanical = _judge_reply(
        [{"check_kind": CheckKind.ORPHAN_TOKEN.value, "section_id": "liability", "detail": "x"}]
    )
    provider = ScriptedProvider([mechanical, mechanical])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    findings = run_judge(db, client, matter=matter, plan=plan, draft=draft, sections=[section])
    # Two calls: the first + the one stricter retry.
    assert len(provider.calls) == 2
    # Fail-visible: a single BLOCKING TONE manual-review finding, NOT a silent clean pass.
    assert len(findings) == 1
    assert findings[0].check_kind == CheckKind.TONE.value
    assert "manual review required" in findings[0].detail


# --------------------------------------------------------------------------------------
# Per-section call count — one judge call per section
# --------------------------------------------------------------------------------------


def test_per_section_call_count(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the visit")
    liability = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    intro = PlannedSection(
        section_id="intro_and_representation",
        purpose="Introduce.",
        allowed_tokens=[],
        required_tokens=[],
        max_words=100,
    )
    plan = StrategyPlan(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        demand_amount_cents=None,
        demand_type="open",
        sections=[liability.model_dump(), intro.model_dump()],
        emphasis_directives=[],
        approved=True,
    )
    db.add(plan)
    db.flush()
    draft = _draft(db, matter, plan)
    sec1 = _draft_one(db, matter, plan, draft, liability, f"Fault from [[{fact}]].")
    sec2 = _draft_one(db, matter, plan, draft, intro, "We represent the claimant.")

    provider = ScriptedProvider([_judge_reply([]), _judge_reply([])])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    findings = run_judge(db, client, matter=matter, plan=plan, draft=draft, sections=[sec1, sec2])
    assert findings == []
    assert len(provider.calls) == 2  # exactly one judge call per section
