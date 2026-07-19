"""Compliance engine tests (M5 Wave C) — the pass, bucket routing, lifecycle, disposition, guard.

Self-contained in-memory engine + firm/users/matter. Tokens are minted via the real registry; a
plan/draft/sections are seeded; a :class:`~app.core.llm_provider.ScriptedProvider` drives the judge
where the semantic pass runs. Synthetic data only — no PHI.

Coverage: ``bucket_for`` exhaustive over every :class:`CheckKind`; the pass short-circuits the judge
on a hard block; dedupe preserves history rows and replaces OPEN ones; disposition refuses a hard
block, is attorney-only, and requires a reason; ``open_blocking_count`` lifecycle math; and the G3
guard integration — an approve refused with open blocking findings, then permitted once resolved
(COMPLIANCE_REVIEW -> PACKAGE_ASSEMBLY, with the registry pin path exercised).
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
from app.engine.compliance import engine as compliance_engine
from app.engine.compliance.engine import (
    HARD_BLOCK_KINDS,
    MECHANICAL_KINDS,
    DispositionActionNotSupported,
    DraftRegistryDrift,
    FindingDispositionForbidden,
    HardBlockNotDisposable,
    bucket_for,
    disposition_finding,
    open_blocking_count,
    run_compliance_pass,
)
from app.engine.orchestrator.service import GuardRefused, apply_gate_action, payload_version
from app.engine.tokenizer import registry
from app.models.enums import (
    CheckKind,
    DraftStatus,
    FindingBucket,
    FindingDisposition,
    FindingStatus,
    GateAction,
    GateState,
)
from app.models.orm import (
    AuditEvent,
    ComplianceFinding,
    DemandDraft,
    DraftSection,
    Firm,
    Matter,
    RegistryVersion,
    StrategyPlan,
    User,
)
from app.models.schemas import FindingActionRequest, GateSubmit, PlannedSection

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
def attorney(db: Session, firm: Firm) -> User:
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
def paralegal(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="paralegal@firm.test",
        display_name="Test Paralegal",
        role="paralegal",
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


def _plan(db: Session, matter: Matter, sections: list) -> StrategyPlan:
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


def _section(
    db: Session,
    matter: Matter,
    draft: DemandDraft,
    *,
    section_id: str,
    body: str,
    render: bool = True,
) -> DraftSection:
    from app.engine.brain2.renderer import render_section

    section = DraftSection(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        purpose="test",
        body_tokenized=body,
        registry_version=draft.registry_version,
        validation="passed",
        sort_order=0,
    )
    db.add(section)
    db.flush()
    if render:
        render_section(db, matter=matter, section=section)
    db.flush()
    return section


def _finding(
    db: Session,
    matter: Matter,
    draft: DemandDraft,
    *,
    check_kind: CheckKind,
    status: FindingStatus = FindingStatus.OPEN,
    section_id: str = "liability",
) -> ComplianceFinding:
    finding = ComplianceFinding(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        registry_version=draft.registry_version,
        check_kind=check_kind.value,
        bucket=bucket_for(check_kind).value,
        severity="blocking",
        detail="planted",
        anchors=[],
        status=status.value,
    )
    db.add(finding)
    db.flush()
    return finding


def _judge_reply(findings: list[dict]) -> CompletionResult:
    return CompletionResult(
        text=json.dumps({"findings": findings}), input_tokens=40, output_tokens=20, cost_cents=1
    )


# --------------------------------------------------------------------------------------
# bucket_for — exhaustive over every CheckKind
# --------------------------------------------------------------------------------------


def test_bucket_for_exhaustive_over_all_check_kinds() -> None:
    for kind in CheckKind:
        bucket = bucket_for(kind)
        expected = FindingBucket.MECHANICAL if kind in MECHANICAL_KINDS else FindingBucket.SEMANTIC
        assert bucket is expected, kind
    # The mechanical set is exactly the enumerated four; every other kind is semantic.
    assert MECHANICAL_KINDS == {
        CheckKind.AMT_LEDGER_MISMATCH,
        CheckKind.MISSING_EXHIBIT,
        CheckKind.MISSING_STATUTORY_TERM,
        CheckKind.PROSE_TOTAL_MISMATCH,
    }


def test_hard_block_kinds_are_the_contract_set() -> None:
    assert HARD_BLOCK_KINDS == {
        CheckKind.ORPHAN_TOKEN,
        CheckKind.AMT_LEDGER_MISMATCH,
        CheckKind.DEAD_ANCHOR,
        CheckKind.MISSING_EXHIBIT,
        CheckKind.UNDISPOSED_ADVERSE,
    }


# --------------------------------------------------------------------------------------
# run_compliance_pass — registry drift, hard-block short-circuit, clean-pass judge
# --------------------------------------------------------------------------------------


def test_pass_refuses_on_registry_drift(db: Session, matter: Matter, attorney: User) -> None:
    fact = _mint_fact(db, matter, attorney, "the incident")
    planned = PlannedSection(
        section_id="liability",
        purpose="p",
        allowed_tokens=[fact],
        required_tokens=[],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    # Bump the matter's registry past the draft's -> drift.
    _mint_fact(db, matter, attorney, "a later fact")
    db.refresh(matter)
    assert draft.registry_version != matter.registry_version
    with pytest.raises(DraftRegistryDrift):
        run_compliance_pass(db, None, matter=matter, draft=draft)


def test_hard_block_short_circuits_judge(db: Session, matter: Matter, attorney: User) -> None:
    planned = PlannedSection(
        section_id="liability",
        purpose="p",
        allowed_tokens=["FACT_999"],
        required_tokens=[],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    # An orphan token -> a hard block -> the judge must NOT run (cheap-first).
    _section(db, matter, draft, section_id="liability", body="Cites [[FACT_999]].", render=False)

    provider = ScriptedProvider([])  # any judge call would exhaust the empty script + fail loudly
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    outcome = run_compliance_pass(db, client, matter=matter, draft=draft)
    assert outcome.hard_blocks >= 1
    assert outcome.judge_skipped is True
    assert provider.calls == []  # the judge never ran
    db.refresh(draft)
    assert draft.status == DraftStatus.IN_COMPLIANCE.value


def test_clean_pass_runs_judge_and_audits(db: Session, matter: Matter, attorney: User) -> None:
    fact = _mint_fact(db, matter, attorney, "the incident")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    # Draft the section via the real drafter so its snapshot is symmetric for the judge.
    from app.engine.brain2.constraints import build_hard_constraints
    from app.engine.brain2.drafter import draft_section

    draft_provider = ScriptedProvider(
        [
            CompletionResult(
                text=json.dumps({"body_tokenized": f"Fault from [[{fact}]]."}),
                input_tokens=10,
                output_tokens=5,
                cost_cents=1,
            )
        ]
    )
    draft_client = MeteredLLMClient(draft_provider, db, matter.firm_id, matter.id)
    section = draft_section(
        db,
        draft_client,
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=build_hard_constraints(db, matter=matter),
        sort_order=0,
    )
    from app.engine.brain2.renderer import render_section

    render_section(db, matter=matter, section=section)
    db.commit()

    provider = ScriptedProvider([_judge_reply([])])
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    outcome = run_compliance_pass(db, client, matter=matter, draft=draft)
    assert outcome.hard_blocks == 0
    assert outcome.judge_skipped is False
    assert outcome.sections_judged == 1
    assert len(provider.calls) == 1  # one judge call for the clean section

    kinds = [
        e.event_kind
        for e in db.execute(
            select(AuditEvent).where(AuditEvent.firm_id == matter.firm_id)
        ).scalars()
    ]
    assert "compliance_pass_completed" in kinds


def test_no_client_marks_judge_skipped(db: Session, matter: Matter, attorney: User) -> None:
    fact = _mint_fact(db, matter, attorney, "the incident")
    planned = PlannedSection(
        section_id="liability",
        purpose="p",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    _section(db, matter, draft, section_id="liability", body=f"Fault from [[{fact}]].")
    outcome = run_compliance_pass(db, None, matter=matter, draft=draft)
    assert outcome.judge_skipped is True
    assert outcome.semantic == 0


# --------------------------------------------------------------------------------------
# Dedupe — a re-pass replaces OPEN findings but preserves history rows
# --------------------------------------------------------------------------------------


def test_dedupe_preserves_history_replaces_open(
    db: Session, matter: Matter, attorney: User
) -> None:
    planned = PlannedSection(
        section_id="liability",
        purpose="p",
        allowed_tokens=["FACT_999"],
        required_tokens=[],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    _section(db, matter, draft, section_id="liability", body="Cites [[FACT_999]].", render=False)

    # A history row (DISPOSITIONED) that must survive a re-pass.
    history = _finding(
        db, matter, draft, check_kind=CheckKind.TONE, status=FindingStatus.DISPOSITIONED
    )
    db.commit()

    run_compliance_pass(db, None, matter=matter, draft=draft)  # first pass -> an OPEN orphan
    open_after_first = list(
        db.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.draft_id == draft.id,
                ComplianceFinding.status == FindingStatus.OPEN.value,
            )
        ).scalars()
    )
    assert any(f.check_kind == CheckKind.ORPHAN_TOKEN.value for f in open_after_first)
    first_open_ids = {f.id for f in open_after_first}

    run_compliance_pass(db, None, matter=matter, draft=draft)  # re-pass -> re-derives OPEN
    # The DISPOSITIONED history row survived.
    db.refresh(history)
    assert history.status == FindingStatus.DISPOSITIONED.value
    # The prior OPEN orphan rows were deleted and re-derived (new ids), not duplicated.
    open_after_second = list(
        db.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.draft_id == draft.id,
                ComplianceFinding.status == FindingStatus.OPEN.value,
            )
        ).scalars()
    )
    assert len(open_after_second) == len(open_after_first)  # replaced, not accumulated
    assert first_open_ids.isdisjoint({f.id for f in open_after_second})


# --------------------------------------------------------------------------------------
# disposition_finding — hard block refused, attorney-only, reason required
# --------------------------------------------------------------------------------------


def test_disposition_hard_block_refused(db: Session, matter: Matter, attorney: User) -> None:
    plan = _plan(db, matter, [])
    draft = _draft(db, matter, plan)
    finding = _finding(db, matter, draft, check_kind=CheckKind.ORPHAN_TOKEN)
    db.commit()
    with pytest.raises(HardBlockNotDisposable):
        disposition_finding(
            db,
            user=attorney,
            finding=finding,
            request=FindingActionRequest(action="override", override_reason="proceed anyway"),
        )


def test_disposition_semantic_requires_attorney(
    db: Session, matter: Matter, paralegal: User
) -> None:
    plan = _plan(db, matter, [])
    draft = _draft(db, matter, plan)
    finding = _finding(db, matter, draft, check_kind=CheckKind.STRATEGY_DRIFT)
    db.commit()
    with pytest.raises(FindingDispositionForbidden):
        disposition_finding(
            db,
            user=paralegal,
            finding=finding,
            request=FindingActionRequest(action="accept", override_reason="looks fine"),
        )


def test_disposition_accept_and_override_set_status(
    db: Session, matter: Matter, attorney: User
) -> None:
    plan = _plan(db, matter, [])
    draft = _draft(db, matter, plan)
    accept_finding = _finding(db, matter, draft, check_kind=CheckKind.TONE)
    override_finding = _finding(db, matter, draft, check_kind=CheckKind.STRATEGY_DRIFT)
    db.commit()

    accepted = disposition_finding(
        db,
        user=attorney,
        finding=accept_finding,
        request=FindingActionRequest(action="accept", override_reason="tone is acceptable here"),
    )
    assert accepted.status == FindingStatus.DISPOSITIONED.value
    assert accepted.disposition == FindingDisposition.ACCEPT.value
    assert accepted.disposition_by == attorney.id

    overridden = disposition_finding(
        db,
        user=attorney,
        finding=override_finding,
        request=FindingActionRequest(action="override", override_reason="deliberate framing"),
    )
    assert overridden.disposition == FindingDisposition.OVERRIDE.value

    kinds = [
        e.event_kind
        for e in db.execute(
            select(AuditEvent).where(AuditEvent.firm_id == matter.firm_id)
        ).scalars()
    ]
    assert kinds.count("compliance_finding_dispositioned") == 2


def test_disposition_rejects_patch_regen_actions(
    db: Session, matter: Matter, attorney: User
) -> None:
    plan = _plan(db, matter, [])
    draft = _draft(db, matter, plan)
    finding = _finding(db, matter, draft, check_kind=CheckKind.STRATEGY_DRIFT)
    db.commit()
    with pytest.raises(DispositionActionNotSupported):
        disposition_finding(
            db,
            user=attorney,
            finding=finding,
            request=FindingActionRequest(action="patch"),
        )


# --------------------------------------------------------------------------------------
# open_blocking_count — lifecycle math
# --------------------------------------------------------------------------------------


def test_open_blocking_count_lifecycle_math(db: Session, matter: Matter, attorney: User) -> None:
    plan = _plan(db, matter, [])
    draft = _draft(db, matter, plan)
    # OPEN + PATCHED + REGENERATED count as blocking; RE_VERIFIED + DISPOSITIONED do not.
    _finding(db, matter, draft, check_kind=CheckKind.STRATEGY_DRIFT, status=FindingStatus.OPEN)
    _finding(db, matter, draft, check_kind=CheckKind.TONE, status=FindingStatus.PATCHED)
    _finding(
        db, matter, draft, check_kind=CheckKind.STRATEGY_DRIFT, status=FindingStatus.REGENERATED
    )
    _finding(db, matter, draft, check_kind=CheckKind.TONE, status=FindingStatus.RE_VERIFIED)
    _finding(
        db, matter, draft, check_kind=CheckKind.STRATEGY_DRIFT, status=FindingStatus.DISPOSITIONED
    )
    db.commit()
    assert open_blocking_count(db, matter=matter, draft=draft) == 3


# --------------------------------------------------------------------------------------
# G3 guard integration — approve refused w/ open blocking, permitted once resolved
# --------------------------------------------------------------------------------------


def _freeze_registry(db: Session, matter: Matter) -> None:
    """Pin the matter's registry version (the G2a freeze the matter passed before G3)."""
    db.add(
        RegistryVersion(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            version=matter.registry_version,
            frozen=True,
            parent_version=None,
            change_reason="g2a_freeze",
        )
    )
    db.commit()


def test_g3_approve_blocked_then_allowed(db: Session, matter: Matter, attorney: User) -> None:
    _freeze_registry(db, matter)
    plan = _plan(db, matter, [])
    draft = _draft(db, matter, plan)
    finding = _finding(db, matter, draft, check_kind=CheckKind.STRATEGY_DRIFT)
    db.commit()

    # With an open blocking finding, the no_blocking_findings guard refuses the G3 approve.
    submit = GateSubmit(
        action=GateAction.APPROVE,
        idempotency_key="g3-attempt-1",
        payload_version=payload_version(db, matter=matter),
    )
    with pytest.raises(GuardRefused) as excinfo:
        apply_gate_action(
            db, matter=matter, user=attorney, gate=GateState.COMPLIANCE_REVIEW.value, submit=submit
        )
    assert excinfo.value.guard == "no_blocking_findings"
    db.refresh(matter)
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value
    db.refresh(draft)
    # WD-2: a refused approve never touches the draft — it stays at its seeded status. This test
    # seeds `validated` via `_draft` (default) and runs no compliance pass, so it is never
    # IN_COMPLIANCE; the point is only that the refusal leaves it unapproved.
    assert draft.status == DraftStatus.VALIDATED.value

    # Resolve the finding (attorney override) -> zero open blocking -> the approve now transitions.
    disposition_finding(
        db,
        user=attorney,
        finding=finding,
        request=FindingActionRequest(
            action="override", override_reason="deliberate framing choice"
        ),
    )
    assert open_blocking_count(db, matter=matter, draft=draft) == 0

    submit2 = GateSubmit(
        action=GateAction.APPROVE,
        idempotency_key="g3-attempt-2",
        payload_version=payload_version(db, matter=matter),
    )
    result = apply_gate_action(
        db, matter=matter, user=attorney, gate=GateState.COMPLIANCE_REVIEW.value, submit=submit2
    )
    assert result.transitioned is True
    db.refresh(matter)
    assert matter.gate_state == GateState.PACKAGE_ASSEMBLY.value
    db.refresh(draft)
    # WD-2: the allowed G3 approve marks the current draft APPROVED (the DraftStatus terminal).
    assert draft.status == DraftStatus.APPROVED.value


def test_post_draft_hook_runs_pass(db: Session, matter: Matter, attorney: User) -> None:
    # The wiring-wave factory: the hook builds a client + runs the pass. With a NullProvider the
    # judge is unavailable (an honest judge_skipped), but the deterministic pass still lands and the
    # draft is IN_COMPLIANCE. The section is drafted via the real drafter so its snapshot is
    # symmetric (else the judge's symmetry gate would drift before reaching the unavailable path).
    from app.engine.brain2.constraints import build_hard_constraints
    from app.engine.brain2.drafter import draft_section
    from app.engine.brain2.renderer import render_section

    fact = _mint_fact(db, matter, attorney, "the incident")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    draft_provider = ScriptedProvider(
        [
            CompletionResult(
                text=json.dumps({"body_tokenized": f"Fault from [[{fact}]]."}),
                input_tokens=10,
                output_tokens=5,
                cost_cents=1,
            )
        ]
    )
    section = draft_section(
        db,
        MeteredLLMClient(draft_provider, db, matter.firm_id, matter.id),
        matter=matter,
        plan=plan,
        draft=draft,
        planned=planned,
        constraints=build_hard_constraints(db, matter=matter),
        sort_order=0,
    )
    render_section(db, matter=matter, section=section)
    db.commit()

    hook = compliance_engine.compliance_post_draft_hook(NullProvider())
    hook(db, matter, draft)
    db.refresh(draft)
    assert draft.status == DraftStatus.IN_COMPLIANCE.value
