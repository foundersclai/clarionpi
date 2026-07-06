"""The tenancy door — central firm-scoping, enforced by construction, not by reviewer eyes.

ClarionPI is a captive multi-firm platform: every ``FirmScoped`` row carries a ``firm_id``
and no read may cross firms. Rather than trust each handler to add a ``WHERE firm_id = ?``
(the per-query-trust failure mode called out in platform_core §4), we attach the predicate
once, at the ORM-execute layer, via a ``do_orm_execute`` listener that injects
``with_loader_criteria(FirmScoped, firm_id == ...)`` into every SELECT.

**Every read path goes through** :func:`scoped_session` — bare ``Session`` usage for reads is
banned by convention (a hub-check grep lands in a later wave). Writes go through
:func:`tenant_add`, which stamps ``firm_id`` and refuses an object already stamped for a
different firm.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.models.orm import FirmScoped

_SCOPE_MARK = "clarionpi_firm_id"


class TenancyViolation(Exception):
    """Raised when an object is added under a firm it does not belong to.

    Carries both ids so the caller/audit can see the mismatch that was refused.
    """

    def __init__(self, *, expected: uuid.UUID, actual: uuid.UUID) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"object carries firm_id={actual} but was added under firm_id={expected}")


def _apply_scope(execute_state: ORMExecuteState) -> None:
    """``do_orm_execute`` hook: inject the firm predicate on every SELECT.

    Only SELECTs are scoped (writes are gated by :func:`tenant_add`), and only when the
    session was marked by :func:`scoped_session`. ``include_aliases=True`` scopes joined
    aliases of the same entity too.
    """
    if not execute_state.is_select:
        return
    firm_id = execute_state.session.info.get(_SCOPE_MARK)
    if firm_id is None:
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            FirmScoped,
            lambda cls: cls.firm_id == firm_id,
            include_aliases=True,
        )
    )


def scoped_session(session: Session, firm_id: uuid.UUID) -> Session:
    """Mark ``session`` so every SELECT is transparently scoped to ``firm_id``.

    Idempotent per session: registers the ``do_orm_execute`` listener once and stamps the
    firm id in ``session.info``. Returns the same session for call-site convenience::

        db = scoped_session(db, current_user.firm_id)
        matter = db.get(Matter, matter_id)  # cannot see another firm's matter
    """
    session.info[_SCOPE_MARK] = firm_id
    if not event.contains(session, "do_orm_execute", _apply_scope):
        event.listen(session, "do_orm_execute", _apply_scope)
    return session


def tenant_add(session: Session, obj: object, firm_id: uuid.UUID) -> None:
    """Stamp ``firm_id`` on a ``FirmScoped`` object and add it, refusing a cross-firm stamp.

    If the object already carries a *different* ``firm_id`` this raises
    :class:`TenancyViolation` — a write can never smuggle a row into another tenant. Non
    firm-scoped objects (e.g. :class:`~app.models.orm.Firm`) are added unchanged.
    """
    if isinstance(obj, FirmScoped):
        existing = getattr(obj, "firm_id", None)
        if existing is not None and existing != firm_id:
            raise TenancyViolation(expected=firm_id, actual=existing)
        obj.firm_id = firm_id
    session.add(obj)
