"""Audit log: inserts succeed; updates and deletes of ``AuditEvent`` are refused (append-only)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.audit import AuditImmutableError, record_event
from app.models.orm import AuditEvent

from .conftest import make_firm


def test_record_event_inserts(session: Session) -> None:
    firm = make_firm(session, "Firm A")
    actor = uuid.uuid4()

    record_event(
        session,
        firm_id=firm.id,
        actor_id=actor,
        event_kind="matter_created",
        payload={"k": "v"},
    )
    session.commit()

    rows = session.query(AuditEvent).all()
    assert len(rows) == 1
    assert rows[0].event_kind == "matter_created"
    assert rows[0].payload == {"k": "v"}


def test_update_of_audit_event_raises(session: Session) -> None:
    firm = make_firm(session, "Firm A")
    event = record_event(session, firm_id=firm.id, actor_id=None, event_kind="x", payload={})
    session.commit()

    event.event_kind = "tampered"
    with pytest.raises(AuditImmutableError):
        session.commit()


def test_delete_of_audit_event_raises(session: Session) -> None:
    firm = make_firm(session, "Firm A")
    event = record_event(session, firm_id=firm.id, actor_id=None, event_kind="x", payload={})
    session.commit()

    session.delete(event)
    with pytest.raises(AuditImmutableError):
        session.commit()
