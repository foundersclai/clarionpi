"""Tenancy door: scoped reads see only their firm; ``tenant_add`` stamps and refuses mismatch."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.tenancy import TenancyViolation, scoped_session, tenant_add
from app.models.enums import GateState
from app.models.orm import Matter

from .conftest import make_firm


def _make_matter(firm_id: uuid.UUID, name: str) -> Matter:
    return Matter(
        firm_id=firm_id,
        client_display_name=name,
        claim_type="mva",
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )


def test_scoped_session_sees_only_own_firms_matters(session: Session) -> None:
    firm_a = make_firm(session, "Firm A")
    firm_b = make_firm(session, "Firm B")
    session.add(_make_matter(firm_a.id, "A-matter"))
    session.add(_make_matter(firm_b.id, "B-matter"))
    session.commit()

    scoped_a = scoped_session(session, firm_a.id)
    visible = scoped_a.query(Matter).all()

    assert [m.client_display_name for m in visible] == ["A-matter"]


def test_scoped_session_get_hides_other_firm_row(session: Session) -> None:
    firm_a = make_firm(session, "Firm A")
    firm_b = make_firm(session, "Firm B")
    b_matter = _make_matter(firm_b.id, "B-matter")
    session.add(b_matter)
    session.commit()
    b_matter_id = b_matter.id
    session.expunge_all()  # force a DB round-trip, not an identity-map hit

    scoped_a = scoped_session(session, firm_a.id)
    assert scoped_a.query(Matter).filter(Matter.id == b_matter_id).one_or_none() is None


def test_tenant_add_stamps_firm_id(session: Session) -> None:
    firm = make_firm(session, "Firm A")
    matter = _make_matter(firm.id, "stamp-me")
    matter.firm_id = None  # type: ignore[assignment]  # unset, let tenant_add stamp it

    tenant_add(session, matter, firm.id)
    session.flush()

    assert matter.firm_id == firm.id


def test_tenant_add_raises_on_firm_mismatch(session: Session) -> None:
    firm_a = make_firm(session, "Firm A")
    firm_b = make_firm(session, "Firm B")
    matter = _make_matter(firm_b.id, "wrong-firm")  # already stamped for B

    with pytest.raises(TenancyViolation) as excinfo:
        tenant_add(session, matter, firm_a.id)

    assert excinfo.value.expected == firm_a.id
    assert excinfo.value.actual == firm_b.id
