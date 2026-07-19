"""Gate-action service tests (M3 Wave B) — service-level, no HTTP.

Fixtures are local to this module (mirroring the corpus conftest style) because
``tests/engine`` is otherwise a pure-unit package with no DB conftest: an in-memory engine,
an open session, the seeded dev users (attorney + paralegal), and a Firm-A matter parked in
``facts_review`` with the two AZ candidates unconfirmed — the G1 entry state.

Coverage: the build_guard_context truth table; per-candidate confirm application (incl.
unknown rule_id + un-confirm); verbatim strategy upsert (weird whitespace preserved, the two
M4 pull-forward fields); the approve map covering exactly the five human gates; gate/state
mismatch; idempotent replay; payload_version staleness; the G2a freeze side-effect; the
override_required / override-with-reason pair (design D2); and atomicity (a refused approve
leaves its edits unapplied).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import DEV_PARALEGAL_ID, get_settings, seed_dev_users
from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.tenancy import tenant_add
from app.engine.orchestrator import service
from app.engine.orchestrator.service import (
    GATE_EVENT_BY_APPROVE,
    GateStateMismatch,
    GuardRefused,
    IllegalGateAction,
    OverrideReasonRequired,
    OverrideRequired,
    StalePayloadVersion,
    UnknownDeadlineRule,
    apply_gate_action,
    build_guard_context,
    payload_version,
)
from app.engine.tokenizer.registry import bump_version
from app.models.enums import (
    CheckKind,
    DeadlineKind,
    DraftStatus,
    FindingStatus,
    FlagKind,
    FlagSeverity,
    GateAction,
    GateEvent,
    GateState,
    RuleVerifyStatus,
    UserRole,
)
from app.models.orm import (
    AuditEvent,
    ComplianceFinding,
    DemandDraft,
    GateRecord,
    IncidentFacts,
    Matter,
    MatterBudget,
    RegistryVersion,
    RiskFlag,
    StrategyInputs,
    User,
)
from app.models.schemas import DeadlineCandidate, GateSubmit

# The two AZ-pack candidates' stable ids (rule_id == statute_cite; see DeadlineConfirmation).
SOL_CITE = "A.R.S. § 12-542 (verify — counsel)"
NOC_CITE = "A.R.S. § 12-821.01 (verify — counsel)"


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin APP_ENV=test so settings (budget default, in-memory DB) match the suite's."""
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


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
def attorney(db: Session) -> User:
    """The seeded dev attorney (Firm A)."""
    seed_dev_users(db)
    return db.execute(select(User).where(User.role == UserRole.ATTORNEY.value)).scalars().first()


@pytest.fixture
def paralegal(db: Session, attorney: User) -> User:
    """The seeded dev paralegal (depends on ``attorney`` so the seed has already run)."""
    return db.get(User, DEV_PARALEGAL_ID)


def _candidates(*, confirmed: tuple[bool, bool] = (False, False)) -> list[dict]:
    """The two AZ candidates as stored JSON dicts, with per-candidate confirmed flags."""
    sol = DeadlineCandidate(
        kind=DeadlineKind.SOL,
        date=dt.date(2028, 1, 15),
        statute_cite=SOL_CITE,
        assumptions=["adult plaintiff — no tolling"],
        verify_status=RuleVerifyStatus.UNVERIFIED,
        confirmed=confirmed[0],
    )
    noc = DeadlineCandidate(
        kind=DeadlineKind.NOTICE_OF_CLAIM,
        date=dt.date(2026, 7, 14),
        statute_cite=NOC_CITE,
        assumptions=["public-entity defendant — confirm at G1"],
        verify_status=RuleVerifyStatus.UNVERIFIED,
        confirmed=confirmed[1],
    )
    return [sol.model_dump(mode="json"), noc.model_dump(mode="json")]


def _sol_only_candidates(*, confirmed: bool = False) -> list[dict]:
    """The post-WD-1 private-party candidate set: SOL only, notice-of-claim suppressed."""
    sol = DeadlineCandidate(
        kind=DeadlineKind.SOL,
        date=dt.date(2028, 1, 15),
        statute_cite=SOL_CITE,
        assumptions=["adult plaintiff — no tolling"],
        verify_status=RuleVerifyStatus.UNVERIFIED,
        confirmed=confirmed,
    )
    return [sol.model_dump(mode="json")]


@pytest.fixture
def matter(db: Session, attorney: User) -> Matter:
    """A Firm-A matter parked at facts_review with both candidates unconfirmed."""
    m = Matter(
        client_display_name="Gate Test Client",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=GateState.FACTS_REVIEW.value,
        registry_version=0,
        sol_candidates=_candidates(),
    )
    tenant_add(db, m, attorney.firm_id)
    db.commit()
    return m


def _submit(
    action: GateAction,
    *,
    key: str,
    version: int,
    edits: dict | None = None,
    reason: str | None = None,
) -> GateSubmit:
    return GateSubmit(
        action=action,
        idempotency_key=key,
        payload_version=version,
        override_reason=reason,
        edits=edits,
    )


def _confirm_all_edits() -> dict:
    return {
        "deadline_confirmations": [
            {"rule_id": SOL_CITE, "confirmed": True},
            {"rule_id": NOC_CITE, "confirmed": True},
        ]
    }


def _records(db: Session, matter: Matter) -> list[GateRecord]:
    return list(
        db.execute(
            select(GateRecord)
            .where(GateRecord.matter_id == matter.id)
            .order_by(GateRecord.created_at, GateRecord.id)
        ).scalars()
    )


# ------------------------------------------------------------------------------------------
# build_guard_context truth table
# ------------------------------------------------------------------------------------------


def test_guard_context_deadlines_all_confirmed(db: Session, attorney: User, matter: Matter) -> None:
    matter.sol_candidates = _candidates(confirmed=(True, True))
    db.commit()
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.deadlines_confirmed is True


def test_guard_context_deadlines_partial_is_false(
    db: Session, attorney: User, matter: Matter
) -> None:
    matter.sol_candidates = _candidates(confirmed=(True, False))
    db.commit()
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.deadlines_confirmed is False


def test_guard_context_deadlines_none_confirmed_is_false(
    db: Session, attorney: User, matter: Matter
) -> None:
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.deadlines_confirmed is False


def test_guard_context_empty_candidate_list_is_false(
    db: Session, attorney: User, matter: Matter
) -> None:
    # Inv 4 / design D1: NO computed deadlines must not slide through G1 as vacuously true.
    matter.sol_candidates = []
    db.commit()
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.deadlines_confirmed is False


def test_guard_context_budget_under_and_at_cap(db: Session, attorney: User, matter: Matter) -> None:
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.budget_available is True  # fresh budget row at the default cap, zero spend

    budget = db.execute(
        select(MatterBudget).where(MatterBudget.matter_id == matter.id)
    ).scalar_one()
    budget.spent_cents = budget.cap_cents  # at cap == exhausted (assert_within_budget is >=)
    db.commit()
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.budget_available is False


def test_guard_context_high_severity_flag_counts(
    db: Session, attorney: User, matter: Matter
) -> None:
    def _flag(severity: FlagSeverity, disposition: str | None) -> RiskFlag:
        flag = RiskFlag(
            matter_id=matter.id,
            kind=FlagKind.TREATMENT_GAP.value,
            severity=severity.value,
            anchors=[],
            detail="t",
            disposition=disposition,
        )
        tenant_add(db, flag, matter.firm_id)
        return flag

    _flag(FlagSeverity.HIGH, None)  # counts
    _flag(FlagSeverity.HIGH, None)  # counts
    _flag(FlagSeverity.HIGH, "address_in_letter")  # dispositioned -> not open
    _flag(FlagSeverity.MEDIUM, None)  # not high
    db.commit()

    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.open_high_severity_flags == 2


def test_guard_context_registry_pin_and_role_and_blocking(
    db: Session, attorney: User, paralegal: User, matter: Matter
) -> None:
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.registry_version_pinned is None  # nothing frozen yet
    assert ctx.registry_version_current == 0
    assert ctx.actor_role is UserRole.ATTORNEY
    assert ctx.blocking_findings == 0  # constant at M3 — compliance lands M5

    # Freeze a version: the pin becomes the latest frozen version.
    row = RegistryVersion(matter_id=matter.id, version=1, frozen=True, change_reason="t")
    tenant_add(db, row, matter.firm_id)
    matter.registry_version = 1
    db.commit()
    ctx = build_guard_context(db, matter=matter, user=paralegal, override_reason=None)
    assert ctx.registry_version_pinned == 1
    assert ctx.actor_role is UserRole.PARALEGAL


# ------------------------------------------------------------------------------------------
# Edits — per-candidate confirm + incident facts + verbatim strategy upsert
# ------------------------------------------------------------------------------------------


def test_confirm_application_per_candidate_and_unconfirm(
    db: Session, attorney: User, matter: Matter
) -> None:
    version = payload_version(db, matter=matter)
    apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.EDIT,
            key="confirm-sol-1",
            version=version,
            edits={"deadline_confirmations": [{"rule_id": SOL_CITE, "confirmed": True}]},
        ),
    )
    db.refresh(matter)
    by_cite = {c["statute_cite"]: c for c in matter.sol_candidates}
    assert by_cite[SOL_CITE]["confirmed"] is True
    assert by_cite[NOC_CITE]["confirmed"] is False  # untouched — confirm is per-candidate

    # Un-confirm is legal (confirmed=False).
    apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.EDIT,
            key="unconfirm-sol-1",
            version=payload_version(db, matter=matter),
            edits={"deadline_confirmations": [{"rule_id": SOL_CITE, "confirmed": False}]},
        ),
    )
    db.refresh(matter)
    assert {c["statute_cite"]: c["confirmed"] for c in matter.sol_candidates} == {
        SOL_CITE: False,
        NOC_CITE: False,
    }


def test_unknown_rule_id_refuses_whole_edit(db: Session, attorney: User, matter: Matter) -> None:
    with pytest.raises(UnknownDeadlineRule) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="facts_review",
            submit=_submit(
                GateAction.EDIT,
                key="bad-rule-1",
                version=payload_version(db, matter=matter),
                edits={
                    "deadline_confirmations": [
                        {"rule_id": SOL_CITE, "confirmed": True},
                        {"rule_id": "A.R.S. § NO-SUCH-RULE", "confirmed": True},
                    ]
                },
            ),
        )
    assert excinfo.value.rule_id == "A.R.S. § NO-SUCH-RULE"
    # The whole action rolled back: the valid confirmation did NOT half-apply.
    db.expire_all()
    assert all(c["confirmed"] is False for c in matter.sol_candidates)
    assert _records(db, matter) == []


# ------------------------------------------------------------------------------------------
# WD-1 — downstream G1 invariant with a SOL-only (suppressed-notice) candidate set (BM-03)
# ------------------------------------------------------------------------------------------


def test_deadlines_confirmed_with_sol_only_candidate(
    db: Session, attorney: User, matter: Matter
) -> None:
    # Post-suppression a private-party MVA matter carries only the SOL candidate; confirming it
    # alone satisfies the G1 non-empty + all-confirmed invariant (the set is never emptied).
    matter.sol_candidates = _sol_only_candidates(confirmed=True)
    db.commit()
    ctx = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx.deadlines_confirmed is True


def test_confirmation_for_suppressed_cite_raises_unknown_rule(
    db: Session, attorney: User, matter: Matter
) -> None:
    # On a SOL-only matter, a confirmation naming the suppressed §12-821.01 notice cite matches
    # no candidate → fail-loud UnknownDeadlineRule (422) + whole-edit rollback, never a silent
    # no-op that resurrects the candidate.
    matter.sol_candidates = _sol_only_candidates()
    db.commit()
    with pytest.raises(UnknownDeadlineRule) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="facts_review",
            submit=_submit(
                GateAction.EDIT,
                key="suppressed-noc-1",
                version=payload_version(db, matter=matter),
                edits={"deadline_confirmations": [{"rule_id": NOC_CITE, "confirmed": True}]},
            ),
        )
    assert excinfo.value.rule_id == NOC_CITE
    db.expire_all()
    assert [c["statute_cite"] for c in matter.sol_candidates] == [SOL_CITE]  # not resurrected
    assert _records(db, matter) == []


def test_incident_facts_merge_creates_then_merges(
    db: Session, attorney: User, matter: Matter
) -> None:
    apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.EDIT,
            key="intake-facts-1",
            version=payload_version(db, matter=matter),
            edits={"incident_facts": {"coverage": "100/300", "adjuster": "J. Doe"}},
        ),
    )
    row = db.execute(select(IncidentFacts).where(IncidentFacts.matter_id == matter.id)).scalar_one()
    assert row.payload == {"coverage": "100/300", "adjuster": "J. Doe"}

    apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.EDIT,
            key="intake-facts-2",
            version=payload_version(db, matter=matter),
            edits={"incident_facts": {"coverage": "250/500"}},
        ),
    )
    db.refresh(row)
    assert row.payload == {"coverage": "250/500", "adjuster": "J. Doe"}  # shallow merge


def test_strategy_upsert_verbatim_including_new_fields(
    db: Session, attorney: User, matter: Matter
) -> None:
    matter.gate_state = GateState.STRATEGY_INTAKE.value
    db.commit()
    weird = "  Rear-end;\tclear liability.\n\n  Emphasize   ER gap.  "
    apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="strategy_intake",
        submit=_submit(
            GateAction.EDIT,
            key="strategy-1",
            version=payload_version(db, matter=matter),
            edits={
                "liability_theory": weird,
                "anchor_amount_cents": 750_000,
                "mmi_date": "2026-06-01",
                "property_damage_estimate_cents": 412_50,
            },
        ),
    )
    row = db.execute(
        select(StrategyInputs).where(StrategyInputs.matter_id == matter.id)
    ).scalar_one()
    assert row.liability_theory == weird  # VERBATIM — no trimming, tabs/newlines preserved
    assert row.anchor_amount_cents == 750_000
    assert row.mmi_date == dt.date(2026, 6, 1)
    assert row.property_damage_estimate_cents == 412_50

    # Second edit with only one field: everything else untouched (non-None-only upsert).
    apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="strategy_intake",
        submit=_submit(
            GateAction.EDIT,
            key="strategy-2",
            version=payload_version(db, matter=matter),
            edits={"injury_framing": "cervical strain w/ radiculopathy"},
        ),
    )
    db.refresh(row)
    assert row.liability_theory == weird
    assert row.injury_framing == "cervical strain w/ radiculopathy"
    assert row.mmi_date == dt.date(2026, 6, 1)


def test_edits_refused_at_non_editable_gate(db: Session, attorney: User, matter: Matter) -> None:
    matter.gate_state = GateState.EVIDENCE_REVIEW.value
    db.commit()
    with pytest.raises(service.EditsNotSupported):
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="evidence_review",
            submit=_submit(
                GateAction.EDIT,
                key="evidence-edit",
                version=payload_version(db, matter=matter),
                edits={"incident_facts": {"a": "b"}},
            ),
        )


# ------------------------------------------------------------------------------------------
# Approve map + mismatch + staleness + replay
# ------------------------------------------------------------------------------------------


def test_approve_map_covers_exactly_the_five_human_gates() -> None:
    # Locked as data: exactly these five (state -> event) pairs are human-approvable, nothing
    # else — a sixth entry or a remap fails here before it can reach a route.
    assert GATE_EVENT_BY_APPROVE == {
        GateState.FACTS_REVIEW: GateEvent.G1_APPROVED,
        GateState.STRATEGY_INTAKE: GateEvent.G15_SUBMITTED,
        GateState.EVIDENCE_REVIEW: GateEvent.G2A_CONFIRMED,
        GateState.PLAN_REVIEW: GateEvent.G25_APPROVED,
        GateState.COMPLIANCE_REVIEW: GateEvent.G3_APPROVED,
    }


def test_approve_on_auto_state_is_illegal(db: Session, attorney: User, matter: Matter) -> None:
    matter.gate_state = GateState.ANALYSIS_RUNNING.value
    db.commit()
    with pytest.raises(IllegalGateAction):
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="analysis_running",
            submit=_submit(
                GateAction.APPROVE,
                key="illegal-approve",
                version=payload_version(db, matter=matter),
            ),
        )
    assert _records(db, matter) == []


def test_gate_state_mismatch(db: Session, attorney: User, matter: Matter) -> None:
    with pytest.raises(GateStateMismatch) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="strategy_intake",  # matter is at facts_review
            submit=_submit(GateAction.EDIT, key="mismatch-1", version=0),
        )
    assert excinfo.value.current == "facts_review"


def test_stale_payload_version_carries_fresh(db: Session, attorney: User, matter: Matter) -> None:
    fresh = payload_version(db, matter=matter)
    with pytest.raises(StalePayloadVersion) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="facts_review",
            submit=_submit(GateAction.EDIT, key="stale-100", version=fresh + 7),
        )
    assert excinfo.value.fresh_version == fresh


def test_idempotent_replay_returns_first_record_once(
    db: Session, attorney: User, matter: Matter
) -> None:
    version = payload_version(db, matter=matter)
    first = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.EDIT,
            key="replay-key-1",
            version=version,
            edits={"deadline_confirmations": [{"rule_id": SOL_CITE, "confirmed": True}]},
        ),
    )
    assert first.replayed is False

    # Same key again (stale version + different edits on purpose): replay wins BEFORE the
    # version fence, no new record, no re-execution, state unchanged.
    second = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.EDIT,
            key="replay-key-1",
            version=version,  # now stale — replay must not care
            edits={"deadline_confirmations": [{"rule_id": SOL_CITE, "confirmed": False}]},
        ),
    )
    assert second.replayed is True
    assert second.transitioned is False
    assert second.record.id == first.record.id
    assert second.to_state == "facts_review"
    assert len(_records(db, matter)) == 1
    db.expire_all()
    by_cite = {c["statute_cite"]: c["confirmed"] for c in matter.sol_candidates}
    assert by_cite[SOL_CITE] is True  # the SECOND submit's un-confirm did NOT execute


# ------------------------------------------------------------------------------------------
# G1 approve path + atomicity
# ------------------------------------------------------------------------------------------


def test_g1_approve_refused_until_all_confirmed_then_transitions(
    db: Session, attorney: User, matter: Matter
) -> None:
    with pytest.raises(GuardRefused) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="facts_review",
            submit=_submit(
                GateAction.APPROVE, key="g1-early-1", version=payload_version(db, matter=matter)
            ),
        )
    assert excinfo.value.guard == "deadlines_confirmed"
    assert excinfo.value.code == "deadlines_unconfirmed"

    # Edits-then-approve in ONE call is legal: confirm all + approve together.
    result = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.APPROVE,
            key="g1-approve-1",
            version=payload_version(db, matter=matter),
            edits=_confirm_all_edits(),
        ),
    )
    assert result.transitioned is True
    assert result.from_state == "facts_review"
    assert result.to_state == "strategy_intake"
    db.expire_all()
    assert matter.gate_state == "strategy_intake"

    # Audit mirror exists for the approve.
    events = list(db.execute(select(AuditEvent)).scalars())
    gate_events = [e for e in events if e.event_kind == "gate_action"]
    assert any(
        e.payload["action"] == "approve" and e.payload["to_state"] == "strategy_intake"
        for e in gate_events
    )


def test_refused_approve_leaves_edits_unapplied_atomically(
    db: Session, paralegal: User, matter: Matter
) -> None:
    # A paralegal submits confirm-all + approve in one call: role_attorney refuses the approve
    # and the transaction rolls back WHOLE — the confirmations must not survive.
    with pytest.raises(GuardRefused) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=paralegal,
            gate="facts_review",
            submit=_submit(
                GateAction.APPROVE,
                key="paralegal-approve",
                version=payload_version(db, matter=matter),
                edits=_confirm_all_edits(),
            ),
        )
    assert excinfo.value.guard == "role_attorney"
    assert excinfo.value.code == "role_not_attorney"
    db.expire_all()
    assert all(c["confirmed"] is False for c in matter.sol_candidates)  # nothing half-applied
    assert _records(db, matter) == []  # no record for a refused action


# ------------------------------------------------------------------------------------------
# G2a: freeze side-effect + override semantics (design D2)
# ------------------------------------------------------------------------------------------


def _park_at_evidence_review(db: Session, matter: Matter, *, registry_version: int = 0) -> None:
    matter.gate_state = GateState.EVIDENCE_REVIEW.value
    matter.registry_version = registry_version
    db.commit()


def test_g2a_approve_freezes_registry_at_current_version(
    db: Session, attorney: User, matter: Matter
) -> None:
    # Drive the registry to v1 the real way (a bump row exists), then park at evidence_review.
    bump_version(db, matter=matter, reason="test_bump")
    db.commit()
    _park_at_evidence_review(db, matter, registry_version=matter.registry_version)

    result = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="evidence_review",
        submit=_submit(
            GateAction.APPROVE, key="g2a-clean-1", version=payload_version(db, matter=matter)
        ),
    )
    assert result.transitioned is True
    assert result.to_state == "plan_review"
    frozen = db.execute(
        select(RegistryVersion).where(
            RegistryVersion.matter_id == matter.id, RegistryVersion.version == 1
        )
    ).scalar_one()
    assert frozen.frozen is True


def test_g2a_freeze_creates_version_row_when_matter_never_bumped(
    db: Session, attorney: User, matter: Matter
) -> None:
    _park_at_evidence_review(db, matter, registry_version=0)
    apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="evidence_review",
        submit=_submit(
            GateAction.APPROVE, key="g2a-v0-freeze", version=payload_version(db, matter=matter)
        ),
    )
    row = db.execute(
        select(RegistryVersion).where(
            RegistryVersion.matter_id == matter.id, RegistryVersion.version == 0
        )
    ).scalar_one()
    assert row.frozen is True
    assert row.change_reason == "g2a_freeze"


def test_g2a_open_high_flag_approve_requires_override_then_override_passes(
    db: Session, attorney: User, matter: Matter
) -> None:
    _park_at_evidence_review(db, matter)
    flag = RiskFlag(
        matter_id=matter.id,
        kind=FlagKind.PREEXISTING_CONDITION.value,
        severity=FlagSeverity.HIGH.value,
        anchors=[],
        detail="prior cervical injury 2024",
    )
    tenant_add(db, flag, matter.firm_id)
    db.commit()

    # Plain approve over an open high-severity flag -> override_required (D2).
    with pytest.raises(OverrideRequired) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="evidence_review",
            submit=_submit(
                GateAction.APPROVE, key="g2a-blocked-1", version=payload_version(db, matter=matter)
            ),
        )
    assert excinfo.value.guard == "high_severity_dispositioned_or_override"
    db.expire_all()
    assert matter.gate_state == "evidence_review"  # parked, not transitioned

    # Override WITHOUT a reason is refused before guards (an override is a reasoned act).
    with pytest.raises(OverrideReasonRequired):
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="evidence_review",
            submit=_submit(
                GateAction.OVERRIDE,
                key="g2a-noreason-1",
                version=payload_version(db, matter=matter),
            ),
        )

    # Override WITH a reason transitions and records the reason (allowed-but-logged).
    result = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="evidence_review",
        submit=_submit(
            GateAction.OVERRIDE,
            key="g2a-override-1",
            version=payload_version(db, matter=matter),
            reason="flag addressed in demand narrative; proceeding",
        ),
    )
    assert result.transitioned is True
    assert result.to_state == "plan_review"
    record = _records(db, matter)[-1]
    assert record.action == GateAction.OVERRIDE.value
    assert record.override_reason == "flag addressed in demand narrative; proceeding"


# ------------------------------------------------------------------------------------------
# Reject parks; dry-run affordances helper
# ------------------------------------------------------------------------------------------


def test_reject_records_without_transition(db: Session, attorney: User, matter: Matter) -> None:
    result = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="facts_review",
        submit=_submit(
            GateAction.REJECT, key="reject-1", version=payload_version(db, matter=matter)
        ),
    )
    assert result.transitioned is False
    assert result.to_state == "facts_review"  # G-gates park on reject
    assert _records(db, matter)[-1].action == GateAction.REJECT.value


def test_dry_run_blockers_name_guards_without_side_effects(
    db: Session, attorney: User, paralegal: User, matter: Matter
) -> None:
    blockers = service.dry_run_approve_blockers(db, matter=matter, user=paralegal)
    assert {b["guard"] for b in blockers} == {"role_attorney", "deadlines_confirmed"}
    assert matter.gate_state == "facts_review"  # nothing moved
    assert _records(db, matter) == []

    matter.sol_candidates = _candidates(confirmed=(True, True))
    db.commit()
    assert service.dry_run_approve_blockers(db, matter=matter, user=attorney) == []


# ------------------------------------------------------------------------------------------
# WD-2 — G3 approve draft side effect (_approve_draft) + fail-loud refusal (BM-01, BM-04)
# ------------------------------------------------------------------------------------------


def _park_at_compliance_review(
    db: Session,
    matter: Matter,
    *,
    draft: bool = True,
    draft_status: DraftStatus = DraftStatus.IN_COMPLIANCE,
    blocking: bool = False,
    registry_version: int = 1,
) -> DemandDraft | None:
    """Park ``matter`` at COMPLIANCE_REVIEW with a frozen registry (so registry_version_match
    passes) and — unless ``draft=False`` — a current draft at ``draft_status``, plus one OPEN
    blocking finding when ``blocking``.

    Mirrors the real pre-G3 state (G2a froze the registry; drafting produced a draft; the
    compliance pass ran). No StrategyPlan is needed: the G3 guards and the ``_approve_draft``
    side effect read only the frozen registry pin + ``latest_draft``. Returns the draft or None.
    """
    from app.engine.compliance.engine import bucket_for

    reg = RegistryVersion(
        matter_id=matter.id, version=registry_version, frozen=True, change_reason="g2a_freeze"
    )
    tenant_add(db, reg, matter.firm_id)
    matter.registry_version = registry_version
    matter.gate_state = GateState.COMPLIANCE_REVIEW.value

    made: DemandDraft | None = None
    if draft:
        made = DemandDraft(
            matter_id=matter.id,
            version=1,
            registry_version=registry_version,
            strategy_plan_version=1,
            status=draft_status.value,
        )
        tenant_add(db, made, matter.firm_id)
        db.flush()  # assign made.id for the finding FK
        if blocking:
            finding = ComplianceFinding(
                draft_id=made.id,
                section_id="liability",
                registry_version=registry_version,
                check_kind=CheckKind.UNDISPOSED_ADVERSE.value,
                bucket=bucket_for(CheckKind.UNDISPOSED_ADVERSE).value,
                severity="blocking",
                detail="planted",
                anchors=[],
                status=FindingStatus.OPEN.value,
            )
            tenant_add(db, finding, matter.firm_id)
    db.commit()
    return made


def _g3(
    action: GateAction, *, key: str, db: Session, matter: Matter, reason: str | None = None
) -> GateSubmit:
    return _submit(action, key=key, version=payload_version(db, matter=matter), reason=reason)


def _gate_audits(db: Session) -> list[AuditEvent]:
    return [e for e in db.execute(select(AuditEvent)).scalars() if e.event_kind == "gate_action"]


def test_side_effects_map_registers_g3_draft_approval() -> None:
    # BM-01 registration: exactly the (COMPLIANCE_REVIEW, G3_APPROVED) entry is added; the two
    # pre-existing entries are unchanged — locked as data so a remap fails here before a route.
    assert (GateState.COMPLIANCE_REVIEW, GateEvent.G3_APPROVED) in service._SIDE_EFFECTS
    assert set(service._SIDE_EFFECTS) == {
        (GateState.EVIDENCE_REVIEW, GateEvent.G2A_CONFIRMED),
        (GateState.PLAN_REVIEW, GateEvent.G25_APPROVED),
        (GateState.COMPLIANCE_REVIEW, GateEvent.G3_APPROVED),
    }


@pytest.mark.parametrize("action", [GateAction.APPROVE, GateAction.OVERRIDE])
def test_g3_action_marks_only_current_draft_approved(
    db: Session, attorney: User, matter: Matter, action: GateAction
) -> None:
    # BM-01 happy: APPROVE and reasoned OVERRIDE on a compliance-passed matter both set the CURRENT
    # draft APPROVED and move to package_assembly, with exactly one GateRecord + one gate_action
    # audit and NO LlmCall / ArtifactSet side effect.
    from app.engine.compliance.engine import latest_draft
    from app.models.orm import ArtifactSet, LlmCall

    _park_at_compliance_review(db, matter, draft=False)  # freeze the registry; seed drafts below
    # Two live drafts: latest_draft picks the HIGHEST version, so ONLY the current (v2) is approved
    # and the older (v1) is left untouched — the target/non-target boundary of "only current".
    older = DemandDraft(
        matter_id=matter.id,
        version=1,
        registry_version=1,
        strategy_plan_version=1,
        status=DraftStatus.IN_COMPLIANCE.value,
    )
    current_draft = DemandDraft(
        matter_id=matter.id,
        version=2,
        registry_version=1,
        strategy_plan_version=1,
        status=DraftStatus.IN_COMPLIANCE.value,
    )
    tenant_add(db, older, matter.firm_id)
    tenant_add(db, current_draft, matter.firm_id)
    db.commit()

    reason = "compliance clean; proceeding" if action is GateAction.OVERRIDE else None
    result = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="compliance_review",
        submit=_g3(action, key=f"g3-{action.value}", db=db, matter=matter, reason=reason),
    )
    assert result.transitioned is True
    assert result.to_state == GateState.PACKAGE_ASSEMBLY.value
    db.expire_all()
    current = latest_draft(db, matter=matter)
    assert current.version == 2  # the CURRENT (highest) draft, never the older one
    assert current.status == DraftStatus.APPROVED.value
    db.refresh(older)
    assert older.status == DraftStatus.IN_COMPLIANCE.value  # the older draft is NOT approved
    assert len(_records(db, matter)) == 1
    assert len(_gate_audits(db)) == 1
    assert db.execute(select(LlmCall)).first() is None
    assert db.execute(select(ArtifactSet)).first() is None


def test_g3_draft_missing_when_highest_draft_superseded(
    db: Session, attorney: User, matter: Matter
) -> None:
    # BM-01 edge: when the highest-version draft is SUPERSEDED, latest_draft returns None (it never
    # falls back to an older draft), so G3 approve fails loud with DraftMissing and rolls back —
    # a distinct path to the same fail-loud contract as the no-draft-at-all case.
    _park_at_compliance_review(db, matter, draft_status=DraftStatus.SUPERSEDED)
    with pytest.raises(GuardRefused) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="compliance_review",
            submit=_g3(GateAction.APPROVE, key="g3-superseded-top", db=db, matter=matter),
        )
    assert excinfo.value.guard == "demand_draft"
    assert excinfo.value.code == "draft_missing"
    db.expire_all()
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value
    assert _records(db, matter) == []


@pytest.mark.parametrize(
    "seed,guard",
    [
        ("role", "role_attorney"),
        ("registry", "registry_version_match"),
        ("blocking", "no_blocking_findings"),
    ],
)
def test_g3_guard_refusals_leave_draft_and_sinks_untouched(
    db: Session, attorney: User, paralegal: User, matter: Matter, seed: str, guard: str
) -> None:
    # BM-01 negative: each pre-dispatch guard refusal (role / registry drift / open blocking) rolls
    # the whole action back — the draft stays IN_COMPLIANCE and no GateRecord/audit is written.
    if seed == "registry":
        draft = _park_at_compliance_review(db, matter)
        matter.registry_version = 2  # pin frozen at 1, current now 2 -> version_mismatch
        db.commit()
        user = attorney
    elif seed == "blocking":
        draft = _park_at_compliance_review(db, matter, blocking=True)
        user = attorney
    else:  # role: a paralegal cannot sign off
        draft = _park_at_compliance_review(db, matter)
        user = paralegal

    with pytest.raises(GuardRefused) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=user,
            gate="compliance_review",
            submit=_g3(GateAction.APPROVE, key=f"g3-refused-{seed}", db=db, matter=matter),
        )
    assert excinfo.value.guard == guard
    db.expire_all()
    db.refresh(draft)
    assert draft.status == DraftStatus.IN_COMPLIANCE.value  # never approved on refusal
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value  # parked
    assert _records(db, matter) == []
    assert _gate_audits(db) == []


def test_g3_override_cannot_bypass_open_blocking_finding(
    db: Session, attorney: User, matter: Matter
) -> None:
    # BM-01: no_blocking_findings is a HARD guard (not the overridable high-severity one) — a
    # reasoned OVERRIDE cannot push past an open blocking finding.
    draft = _park_at_compliance_review(db, matter, blocking=True)
    with pytest.raises(GuardRefused) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="compliance_review",
            submit=_g3(
                GateAction.OVERRIDE,
                key="g3-override-blocked",
                db=db,
                matter=matter,
                reason="I really want to ship",
            ),
        )
    assert excinfo.value.guard == "no_blocking_findings"
    db.expire_all()
    db.refresh(draft)
    assert draft.status == DraftStatus.IN_COMPLIANCE.value
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value


def test_g3_draft_missing_refuses_atomically(db: Session, attorney: User, matter: Matter) -> None:
    # BM-01 edge: guards pass (no draft -> no_blocking_findings counts zero) but the side effect
    # finds no current draft -> fail-loud DraftMissing (a GuardRefused with the exact wire code),
    # rolling the whole action back. Unreachable on the normal path (COMPLIANCE_REVIEW is
    # post-DRAFTING); constructed here by parking with no draft.
    _park_at_compliance_review(db, matter, draft=False)
    with pytest.raises(GuardRefused) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="compliance_review",
            submit=_g3(GateAction.APPROVE, key="g3-no-draft", db=db, matter=matter),
        )
    assert excinfo.value.guard == "demand_draft"
    assert excinfo.value.code == "draft_missing"
    db.expire_all()
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value  # not moved
    assert _records(db, matter) == []


def test_g3_audit_failure_rolls_back_draft_status_gate_and_records(
    db: Session, attorney: User, matter: Matter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BM-01 terminal: the draft-status write shares the ONE gate transaction. An audit failure
    # AFTER the status mutation rolls the draft, gate_state, and GateRecord back together.
    draft = _park_at_compliance_review(db, matter)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(service, "record_event", _boom)
    with pytest.raises(RuntimeError):
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="compliance_review",
            submit=_g3(GateAction.APPROVE, key="g3-audit-fail", db=db, matter=matter),
        )
    db.expire_all()
    db.refresh(draft)
    assert draft.status == DraftStatus.IN_COMPLIANCE.value  # rolled back with the transaction
    assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value
    assert _records(db, matter) == []


def test_missing_g3_side_effect_logs_diagnostic(
    db: Session,
    attorney: User,
    matter: Matter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # BM-01 fail-visible wiring guard: if a future regression drops the G3 map entry, a successful
    # G3 dispatch that resolves NO side effect logs a clarionpi.orchestrator ERROR (ids/state only)
    # so the silent-unapproved bug can never recur unseen. Simulated by removing the entry.
    import logging

    draft = _park_at_compliance_review(db, matter)
    patched = {
        k: v
        for k, v in service._SIDE_EFFECTS.items()
        if k != (GateState.COMPLIANCE_REVIEW, GateEvent.G3_APPROVED)
    }
    monkeypatch.setattr(service, "_SIDE_EFFECTS", patched)
    with caplog.at_level(logging.ERROR, logger="clarionpi.orchestrator"):
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="compliance_review",
            submit=_g3(GateAction.APPROVE, key="g3-missing-sidefx", db=db, matter=matter),
        )
    assert any(
        r.name == "clarionpi.orchestrator" and r.levelno == logging.ERROR for r in caplog.records
    )
    db.expire_all()
    db.refresh(draft)
    assert draft.status == DraftStatus.IN_COMPLIANCE.value  # no side effect ran -> unapproved


def test_duplicate_successful_g3_approve_mismatches_before_replay(
    db: Session, attorney: User, matter: Matter
) -> None:
    # BM-04: after a successful G3 approve the matter is at package_assembly; a duplicate submit
    # addressed to compliance_review hits GateStateMismatch BEFORE the replay lookup — one
    # GateRecord only, the approved draft unchanged.
    from app.engine.compliance.engine import latest_draft

    _park_at_compliance_review(db, matter)
    first = apply_gate_action(
        db,
        matter=matter,
        user=attorney,
        gate="compliance_review",
        submit=_g3(GateAction.APPROVE, key="g3-duplicate", db=db, matter=matter),
    )
    assert first.to_state == GateState.PACKAGE_ASSEMBLY.value
    db.expire_all()
    with pytest.raises(GateStateMismatch) as excinfo:
        apply_gate_action(
            db,
            matter=matter,
            user=attorney,
            gate="compliance_review",
            submit=_submit(
                GateAction.APPROVE, key="g3-duplicate", version=payload_version(db, matter=matter)
            ),
        )
    assert excinfo.value.current == GateState.PACKAGE_ASSEMBLY.value
    assert len(_records(db, matter)) == 1
    assert latest_draft(db, matter=matter).status == DraftStatus.APPROVED.value
