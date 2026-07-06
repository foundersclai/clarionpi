"""Risk-flag disposition tests — role gating (inv 8), audit, re-disposition, guard parity.

The last test imports the REAL ``app.engine.orchestrator.service.build_guard_context`` — the
integration point that makes G2a confirm work: after an attorney dispositions the only HIGH flag,
the guard context must report ``open_high_severity_flags == 0`` (the shared definition
``open_high_severity_count`` mirrors).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.engine.brain1.risk import (
    HighSeverityDispositionForbidden,
    disposition_flag,
    open_high_severity_count,
)
from app.engine.orchestrator.service import build_guard_context
from app.models.enums import FlagDisposition, FlagKind, FlagSeverity, GateState, UserRole
from app.models.orm import AuditEvent, Firm, Matter, RiskFlag, User
from app.models.schemas import FlagDispositionRequest

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
def matter(db: Session, firm: Firm) -> Matter:
    m = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Jane Doe",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.EVIDENCE_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()
    return m


def _user(db: Session, firm: Firm, role: UserRole) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email=f"{role.value}@firm.test",
        display_name=f"Test {role.value}",
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


def _flag(db: Session, matter: Matter, *, severity: FlagSeverity, kind: FlagKind) -> RiskFlag:
    flag = RiskFlag(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        kind=kind.value,
        severity=severity.value,
        anchors=[],
        detail="test flag",
    )
    db.add(flag)
    db.commit()
    return flag


def _audit(db: Session) -> list[AuditEvent]:
    return list(
        db.execute(
            select(AuditEvent).where(AuditEvent.event_kind == "risk_flag_dispositioned")
        ).scalars()
    )


# --------------------------------------------------------------------------------------
# Role gating (inv 8)
# --------------------------------------------------------------------------------------


def test_paralegal_dispositions_medium_ok(db: Session, matter: Matter, paralegal: User) -> None:
    flag = _flag(db, matter, severity=FlagSeverity.MEDIUM, kind=FlagKind.LOW_PROPERTY_DAMAGE)
    req = FlagDispositionRequest(disposition=FlagDisposition.ADDRESS_IN_LETTER)
    out = disposition_flag(db, user=paralegal, flag=flag, request=req)
    assert out.disposition == FlagDisposition.ADDRESS_IN_LETTER.value
    assert out.disposition_by == paralegal.id
    assert out.disposition_role == UserRole.PARALEGAL.value  # role recorded


def test_paralegal_on_high_is_forbidden(db: Session, matter: Matter, paralegal: User) -> None:
    flag = _flag(db, matter, severity=FlagSeverity.HIGH, kind=FlagKind.PREEXISTING_CONDITION)
    req = FlagDispositionRequest(disposition=FlagDisposition.ADDRESS_IN_LETTER)
    with pytest.raises(HighSeverityDispositionForbidden) as exc:
        disposition_flag(db, user=paralegal, flag=flag, request=req)
    assert exc.value.required_role == UserRole.ATTORNEY.value
    assert exc.value.actual == UserRole.PARALEGAL.value
    # Nothing was written.
    db.refresh(flag)
    assert flag.disposition is None
    assert _audit(db) == []


def test_attorney_on_high_ok_and_audits(db: Session, matter: Matter, attorney: User) -> None:
    flag = _flag(db, matter, severity=FlagSeverity.HIGH, kind=FlagKind.CAUSATION_AMBIGUITY)
    req = FlagDispositionRequest(
        disposition=FlagDisposition.OMIT_WITH_RATIONALE, rationale="addressed at mediation instead"
    )
    out = disposition_flag(db, user=attorney, flag=flag, request=req)
    assert out.disposition == FlagDisposition.OMIT_WITH_RATIONALE.value
    assert out.disposition_role == UserRole.ATTORNEY.value
    assert out.disposition_rationale == "addressed at mediation instead"

    events = _audit(db)
    assert len(events) == 1
    assert events[0].payload["flag_id"] == str(flag.id)
    assert events[0].payload["severity"] == FlagSeverity.HIGH.value
    assert events[0].payload["disposition"] == FlagDisposition.OMIT_WITH_RATIONALE.value
    assert events[0].payload["role"] == UserRole.ATTORNEY.value


# --------------------------------------------------------------------------------------
# Schema-level rationale gate passes through the service
# --------------------------------------------------------------------------------------


def test_omit_without_rationale_rejected_at_schema() -> None:
    # The service trusts the schema's omit-requires-rationale validator; assert it fires before a
    # request even reaches disposition_flag.
    with pytest.raises(ValueError):
        FlagDispositionRequest(disposition=FlagDisposition.OMIT_WITH_RATIONALE)


# --------------------------------------------------------------------------------------
# Re-disposition (pre-freeze mind-change) overwrites + fresh audit
# --------------------------------------------------------------------------------------


def test_re_disposition_overwrites_and_re_audits(
    db: Session, matter: Matter, attorney: User
) -> None:
    flag = _flag(db, matter, severity=FlagSeverity.HIGH, kind=FlagKind.PRIOR_CLAIM)
    disposition_flag(
        db,
        user=attorney,
        flag=flag,
        request=FlagDispositionRequest(disposition=FlagDisposition.ADDRESS_IN_LETTER),
    )
    # Attorney changes their mind pre-freeze.
    disposition_flag(
        db,
        user=attorney,
        flag=flag,
        request=FlagDispositionRequest(
            disposition=FlagDisposition.OMIT_WITH_RATIONALE, rationale="reconsidered"
        ),
    )
    db.refresh(flag)
    assert flag.disposition == FlagDisposition.OMIT_WITH_RATIONALE.value  # overwritten
    assert flag.disposition_rationale == "reconsidered"
    assert len(_audit(db)) == 2  # two audit events, one per disposition act


# --------------------------------------------------------------------------------------
# open_high_severity_count + guard parity (the G2a-confirm integration point)
# --------------------------------------------------------------------------------------


def test_open_high_severity_count_matches_definition(
    db: Session, matter: Matter, attorney: User
) -> None:
    high_open = _flag(db, matter, severity=FlagSeverity.HIGH, kind=FlagKind.PREEXISTING_CONDITION)
    _flag(db, matter, severity=FlagSeverity.MEDIUM, kind=FlagKind.DEGENERATIVE_FINDING)  # not high
    assert open_high_severity_count(db, matter=matter) == 1  # only the open HIGH counts

    # Disposition the HIGH flag -> the open-high count drops to zero.
    disposition_flag(
        db,
        user=attorney,
        flag=high_open,
        request=FlagDispositionRequest(disposition=FlagDisposition.ADDRESS_IN_LETTER),
    )
    assert open_high_severity_count(db, matter=matter) == 0


def test_guard_context_open_high_parity(db: Session, matter: Matter, attorney: User) -> None:
    flag = _flag(db, matter, severity=FlagSeverity.HIGH, kind=FlagKind.LIABILITY_WEAKNESS)
    ctx_before = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx_before.open_high_severity_flags == 1
    assert open_high_severity_count(db, matter=matter) == 1  # the two agree

    disposition_flag(
        db,
        user=attorney,
        flag=flag,
        request=FlagDispositionRequest(disposition=FlagDisposition.ADDRESS_IN_LETTER),
    )
    ctx_after = build_guard_context(db, matter=matter, user=attorney, override_reason=None)
    assert ctx_after.open_high_severity_flags == 0  # G2a confirm can now proceed clean
    assert open_high_severity_count(db, matter=matter) == 0
