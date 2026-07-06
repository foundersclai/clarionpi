"""Append-only audit log (invariant 9).

Two guarantees live here:

* :func:`record_event` is the *only* way audit rows are written — a typed helper that stamps
  the firm and inserts an :class:`~app.models.orm.AuditEvent`.
* Immutability is enforced structurally: a ``before_flush`` listener on the ``Session`` class
  raises :class:`AuditImmutableError` if any :class:`AuditEvent` appears in ``session.dirty``
  (an update) or ``session.deleted`` (a delete). The log has no mutate path by construction,
  not by convention — matching platform_core §4 ("no update/delete path exists").

The listener is armed at import time on the base ``Session`` class, so it covers every session
the app creates.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.orm import AuditEvent


class AuditImmutableError(Exception):
    """Raised when a flush would update or delete an append-only ``AuditEvent`` row."""


def _guard_audit_immutability(session: Session, flush_context: object, instances: object) -> None:
    """``before_flush`` hook: refuse any update/delete of an ``AuditEvent``.

    Inserts (``session.new``) are allowed; only ``dirty`` (updates) and ``deleted`` are
    refused. Raising here aborts the flush before any SQL is emitted.
    """
    for obj in session.dirty:
        if isinstance(obj, AuditEvent) and session.is_modified(obj):
            raise AuditImmutableError("audit_events is append-only: update refused")
    for obj in session.deleted:
        if isinstance(obj, AuditEvent):
            raise AuditImmutableError("audit_events is append-only: delete refused")


# Arm the guard once, for every Session in the process.
event.listen(Session, "before_flush", _guard_audit_immutability)


def record_event(
    session: Session,
    *,
    firm_id: uuid.UUID,
    event_kind: str,
    actor_id: uuid.UUID | None,
    payload: dict,
) -> AuditEvent:
    """Insert an append-only audit event and return the (unflushed) row.

    The write is synchronous and transactional with the caller's action (invariant 9: an
    audit-write failure fails the action). The row is added to ``session`` but not committed
    here — it commits with the surrounding unit of work.
    """
    audit_event = AuditEvent(
        firm_id=firm_id,
        event_kind=event_kind,
        actor_id=actor_id,
        payload=payload,
    )
    session.add(audit_event)
    return audit_event
