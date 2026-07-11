"""apply_registry_bump / reconcile_matter_cursor (BUS-05) — the invalidation service.

Exact-state coverage over the matrix, idempotent replay via the durable cursor, stale-marker
writes (plans invalidated, drafts superseded — historical rows preserved), the package_ready
immutable-new-cycle record, crash recovery (a lagging cursor re-applies), and the legacy
NULL-cursor reconciliation for both stale and already-current rows.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.engine.orchestrator.invalidation import INVALIDATION, Effect
from app.engine.orchestrator.registry_bump import (
    apply_registry_bump,
    packaged_registry_version,
    reconcile_matter_cursor,
)
from app.models.enums import DraftStatus, GateState, UserRole
from app.models.orm import (
    ArtifactSet,
    AuditEvent,
    DemandDraft,
    Firm,
    Matter,
    StrategyPlan,
    User,
)


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
def db(engine: Engine) -> Iterator[Session]:
    factory = create_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def firm(db: Session) -> Firm:
    f = Firm(id=uuid.uuid4(), name="Bump Firm")
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def attorney(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email=f"bump-{uuid.uuid4().hex[:8]}@firm.test",
        display_name="Attorney",
        role=UserRole.ATTORNEY.value,
    )
    db.add(u)
    db.flush()
    return u


def _matter(db: Session, firm: Firm, *, state: GateState, registry_version: int = 3) -> Matter:
    m = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Bump Client",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=state.value,
        registry_version=registry_version,
        sol_candidates=[],
        invalidation_applied_registry_version=1,
    )
    db.add(m)
    db.commit()
    return m


def _plan(
    db: Session, matter: Matter, *, registry_version: int, approved: bool = True
) -> StrategyPlan:
    plan = StrategyPlan(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=registry_version,
        sections=[],
        demand_amount_cents=None,
        demand_type="open",
        emphasis_directives=[],
        approved=approved,
    )
    db.add(plan)
    db.commit()
    return plan


def _draft(
    db: Session,
    matter: Matter,
    *,
    registry_version: int,
    version: int = 1,
    status: str = "approved",
) -> DemandDraft:
    d = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=version,
        registry_version=registry_version,
        strategy_plan_version=1,
        status=status,
        memo="",
    )
    db.add(d)
    db.commit()
    return d


@pytest.mark.parametrize(
    ("state", "expected_to"),
    [
        (GateState.PLAN_REVIEW, GateState.EVIDENCE_REVIEW),
        (GateState.DRAFTING, GateState.EVIDENCE_REVIEW),
        (GateState.COMPLIANCE_REVIEW, GateState.EVIDENCE_REVIEW),
        (GateState.PACKAGE_ASSEMBLY, GateState.EVIDENCE_REVIEW),
        (GateState.EVIDENCE_REVIEW, GateState.EVIDENCE_REVIEW),
        (GateState.CORPUS_PROCESSING, GateState.CORPUS_PROCESSING),
        (GateState.ANALYSIS_RUNNING, GateState.ANALYSIS_RUNNING),
        (GateState.FACTS_REVIEW, GateState.FACTS_REVIEW),
        (GateState.STRATEGY_INTAKE, GateState.STRATEGY_INTAKE),
    ],
)
def test_every_transitionable_state_lands_where_the_matrix_says(
    db: Session, firm: Firm, attorney: User, state: GateState, expected_to: GateState
) -> None:
    matter = _matter(db, firm, state=state)
    outcome = apply_registry_bump(db, matter=matter, user=attorney, to_registry_version=3)
    assert outcome.applied is True
    assert outcome.effect == INVALIDATION[state]
    db.refresh(matter)
    assert matter.gate_state == expected_to.value
    assert matter.invalidation_applied_registry_version == 3
    kinds = [e.event_kind for e in db.scalars(select(AuditEvent))]
    assert "registry_bump_applied" in kinds


def test_package_ready_records_immutable_new_cycle_without_transition(
    db: Session, firm: Firm, attorney: User
) -> None:
    matter = _matter(db, firm, state=GateState.PACKAGE_READY)
    outcome = apply_registry_bump(db, matter=matter, user=attorney, to_registry_version=3)
    assert outcome.applied is True
    assert outcome.effect == Effect.IMMUTABLE_NEW_CYCLE
    db.refresh(matter)
    assert matter.gate_state == GateState.PACKAGE_READY.value  # untouched
    assert matter.invalidation_applied_registry_version == 3


def test_bump_invalidates_all_stale_plans_and_supersedes_drafts(
    db: Session, firm: Firm, attorney: User
) -> None:
    matter = _matter(db, firm, state=GateState.PLAN_REVIEW, registry_version=5)
    stale_plan = _plan(db, matter, registry_version=3)
    current_plan = _plan(db, matter, registry_version=5)
    stale_draft = _draft(db, matter, registry_version=3, version=1)
    already_superseded = _draft(
        db, matter, registry_version=2, version=2, status=DraftStatus.SUPERSEDED.value
    )

    outcome = apply_registry_bump(db, matter=matter, user=attorney, to_registry_version=5)
    assert outcome.plans_invalidated == 1
    assert outcome.drafts_superseded == 1
    db.refresh(stale_plan)
    db.refresh(current_plan)
    db.refresh(stale_draft)
    db.refresh(already_superseded)
    # The stale plan is marked but PRESERVED (historical evidence — approved flag intact).
    assert stale_plan.invalidated_by_registry_version == 5
    assert stale_plan.approved is True
    assert current_plan.invalidated_by_registry_version is None
    assert stale_draft.status == DraftStatus.SUPERSEDED.value
    assert already_superseded.status == DraftStatus.SUPERSEDED.value  # idempotent


def test_covered_cursor_is_an_idempotent_no_op(db: Session, firm: Firm, attorney: User) -> None:
    matter = _matter(db, firm, state=GateState.PLAN_REVIEW)
    first = apply_registry_bump(db, matter=matter, user=attorney, to_registry_version=3)
    assert first.applied is True
    db.refresh(matter)
    matter.gate_state = GateState.PLAN_REVIEW.value  # simulate later forward progress
    db.commit()
    replay = apply_registry_bump(db, matter=matter, user=attorney, to_registry_version=3)
    assert replay.applied is False
    db.refresh(matter)
    assert matter.gate_state == GateState.PLAN_REVIEW.value  # replay moved nothing


def test_lagging_cursor_recovers_after_simulated_crash(
    db: Session, firm: Firm, attorney: User
) -> None:
    """Registry sync committed (matter.registry_version=4) but invalidation crashed before
    running (cursor stays 1): a retry with no pending documents must still apply it."""
    matter = _matter(db, firm, state=GateState.DRAFTING, registry_version=4)
    _draft(db, matter, registry_version=2)
    outcome = apply_registry_bump(db, matter=matter, user=attorney, to_registry_version=4)
    assert outcome.applied is True
    db.refresh(matter)
    assert matter.gate_state == GateState.EVIDENCE_REVIEW.value
    assert matter.invalidation_applied_registry_version == 4


def test_reconcile_applies_bump_for_stale_legacy_matter(
    db: Session, firm: Firm, attorney: User
) -> None:
    matter = _matter(db, firm, state=GateState.PLAN_REVIEW, registry_version=6)
    matter.invalidation_applied_registry_version = None  # legacy pre-fix row
    db.commit()
    _plan(db, matter, registry_version=4)  # current-usable plan OLDER than the registry
    outcome = reconcile_matter_cursor(db, matter=matter, user=attorney)
    assert outcome is not None and outcome.applied is True
    db.refresh(matter)
    assert matter.gate_state == GateState.EVIDENCE_REVIEW.value  # NOT grandfathered
    assert matter.invalidation_applied_registry_version == 6


def test_reconcile_initializes_cursor_for_current_legacy_matter(
    db: Session, firm: Firm, attorney: User
) -> None:
    matter = _matter(db, firm, state=GateState.PLAN_REVIEW, registry_version=6)
    matter.invalidation_applied_registry_version = None
    db.commit()
    _plan(db, matter, registry_version=6)  # derived state matches — nothing stale
    outcome = reconcile_matter_cursor(db, matter=matter, user=attorney)
    assert outcome is None
    db.refresh(matter)
    assert matter.gate_state == GateState.PLAN_REVIEW.value
    assert matter.invalidation_applied_registry_version == 6


def test_packaged_registry_version_reads_latest_set(
    db: Session, firm: Firm, attorney: User
) -> None:
    matter = _matter(db, firm, state=GateState.PACKAGE_READY, registry_version=5)
    draft = _draft(db, matter, registry_version=3)
    db.add(
        ArtifactSet(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            draft_id=draft.id,
            draft_version=1,
            registry_version=3,
            artifacts=[],
            built_by=attorney.id,
        )
    )
    db.commit()
    assert packaged_registry_version(db, matter=matter) == 3
