"""START_CYCLE — the explicit package_ready → evidence_review replacement cycle (BUS-05).

Service-level: attorney-only + registry-newer-than-packaged guards, GateRecord + audit via
the shared gate-action machinery, post-transition idempotent replay (the START_CYCLE-specific
replay runs BEFORE the gate-state check — a retry after success must not surface
gate_state_mismatch), and the prior artifact rows staying untouched.
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
from app.engine.orchestrator.service import (
    GateStateMismatch,
    GuardRefused,
    apply_gate_action,
)
from app.models.enums import GateState, UserRole
from app.models.orm import ArtifactSet, DemandDraft, Firm, GateRecord, Matter, User
from app.models.schemas import GateSubmit


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
    s = create_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def firm(db: Session) -> Firm:
    f = Firm(id=uuid.uuid4(), name="Cycle Firm")
    db.add(f)
    db.flush()
    return f


def _user(db: Session, firm: Firm, role: UserRole) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email=f"{role.value}-{uuid.uuid4().hex[:8]}@firm.test",
        display_name=role.value,
        role=role.value,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def attorney(db: Session, firm: Firm) -> User:
    return _user(db, firm, UserRole.ATTORNEY)


@pytest.fixture
def paralegal(db: Session, firm: Firm) -> User:
    return _user(db, firm, UserRole.PARALEGAL)


@pytest.fixture
def packaged_matter(db: Session, firm: Firm, attorney: User) -> Matter:
    """A package_ready matter whose registry (5) outran its packaged draft (3)."""
    m = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Cycle Client",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.PACKAGE_READY.value,
        registry_version=5,
        sol_candidates=[],
        invalidation_applied_registry_version=5,
    )
    db.add(m)
    db.flush()
    draft = DemandDraft(
        id=uuid.uuid4(),
        firm_id=firm.id,
        matter_id=m.id,
        version=1,
        registry_version=3,
        strategy_plan_version=1,
        status="approved",
        memo="",
    )
    db.add(draft)
    db.flush()
    db.add(
        ArtifactSet(
            id=uuid.uuid4(),
            firm_id=firm.id,
            matter_id=m.id,
            draft_id=draft.id,
            draft_version=1,
            registry_version=3,
            artifacts=[{"kind": "letter_docx", "object_key": "k", "sha256": "x", "byte_count": 1}],
            built_by=attorney.id,
        )
    )
    db.commit()
    return m


def _submit(key: str, version: int) -> GateSubmit:
    return GateSubmit.model_validate(
        {"action": "start_cycle", "idempotency_key": key, "payload_version": version}
    )


def _payload_version(db: Session, matter: Matter) -> int:
    from app.engine.orchestrator.service import payload_version

    return payload_version(db, matter=matter)


def test_attorney_starts_cycle_and_artifacts_survive(
    db: Session, packaged_matter: Matter, attorney: User
) -> None:
    version = _payload_version(db, packaged_matter)
    result = apply_gate_action(
        db,
        matter=packaged_matter,
        user=attorney,
        gate=GateState.PACKAGE_READY.value,
        submit=_submit("cycle-start-1", version),
    )
    assert result.transitioned is True
    assert result.to_state == GateState.EVIDENCE_REVIEW.value
    db.refresh(packaged_matter)
    assert packaged_matter.gate_state == GateState.EVIDENCE_REVIEW.value
    # The GateRecord carries the typed action; the prior artifact set is untouched.
    record = db.execute(
        select(GateRecord).where(GateRecord.idempotency_key == "cycle-start-1")
    ).scalar_one()
    assert record.action == "start_cycle"
    assert record.gate == GateState.PACKAGE_READY.value
    sets = list(db.scalars(select(ArtifactSet)))
    assert len(sets) == 1 and sets[0].registry_version == 3  # immutable historical output


def test_paralegal_cannot_start_cycle(
    db: Session, packaged_matter: Matter, paralegal: User
) -> None:
    version = _payload_version(db, packaged_matter)
    with pytest.raises(GuardRefused) as exc:
        apply_gate_action(
            db,
            matter=packaged_matter,
            user=paralegal,
            gate=GateState.PACKAGE_READY.value,
            submit=_submit("cycle-paralegal", version),
        )
    assert exc.value.code == "role_not_attorney"
    db.refresh(packaged_matter)
    assert packaged_matter.gate_state == GateState.PACKAGE_READY.value


def test_cycle_refused_when_registry_not_newer_than_packaged(
    db: Session, packaged_matter: Matter, attorney: User
) -> None:
    packaged_matter.registry_version = 3  # nothing new since packaging
    db.commit()
    version = _payload_version(db, packaged_matter)
    with pytest.raises(GuardRefused) as exc:
        apply_gate_action(
            db,
            matter=packaged_matter,
            user=attorney,
            gate=GateState.PACKAGE_READY.value,
            submit=_submit("cycle-not-newer", version),
        )
    assert exc.value.code == "registry_not_newer"


def test_post_transition_retry_replays_instead_of_gate_mismatch(
    db: Session, packaged_matter: Matter, attorney: User
) -> None:
    """The START_CYCLE-specific replay runs BEFORE the gate-state check: after a successful
    cycle start moved the matter to evidence_review, a duplicate submission (same
    idempotency key, still addressed to package_ready) replays the original record."""
    version = _payload_version(db, packaged_matter)
    first = apply_gate_action(
        db,
        matter=packaged_matter,
        user=attorney,
        gate=GateState.PACKAGE_READY.value,
        submit=_submit("cycle-retry-key", version),
    )
    assert first.transitioned is True

    retry = apply_gate_action(
        db,
        matter=packaged_matter,
        user=attorney,
        gate=GateState.PACKAGE_READY.value,  # the client's stale view
        submit=_submit("cycle-retry-key", version),
    )
    assert retry.replayed is True
    assert retry.record.id == first.record.id
    db.refresh(packaged_matter)
    assert packaged_matter.gate_state == GateState.EVIDENCE_REVIEW.value  # replay moved nothing


def test_fresh_key_after_transition_still_gets_gate_mismatch(
    db: Session, packaged_matter: Matter, attorney: User
) -> None:
    version = _payload_version(db, packaged_matter)
    apply_gate_action(
        db,
        matter=packaged_matter,
        user=attorney,
        gate=GateState.PACKAGE_READY.value,
        submit=_submit("cycle-key-aaaa", version),
    )
    with pytest.raises(GateStateMismatch):
        apply_gate_action(
            db,
            matter=packaged_matter,
            user=attorney,
            gate=GateState.PACKAGE_READY.value,  # stale: the matter moved on
            submit=_submit("cycle-key-bbbb", version + 1),
        )
