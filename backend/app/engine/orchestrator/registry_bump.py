"""Registry-bump orchestration (BUS-05) — ONE owner for "new facts arrived, what goes stale?".

When late documents bump the fact registry while a matter sits mid-pipeline, this service
applies the flow_04 invalidation matrix atomically: it row-locks and REFRESHES the matter
(a concurrent gate action serializes on the same lock — see ``apply_gate_action``), marks
every stale plan/draft, moves the gate along the machine's ``REGISTRY_BUMPED`` edge, writes
the audit trail, and advances the durable cursor — all in one transaction.

The cursor (``Matter.invalidation_applied_registry_version``) is what makes this
crash-recoverable: registry sync COMMITS before the gate step, so a crash in between leaves
the cursor lagging and the next completion/retry re-attempts the missed invalidation, even
when no documents remain pending. A NULL cursor marks a legacy row that predates the fix —
:func:`reconcile_matter_cursor` evaluates (never grandfathers) it on first touch (ADR-0012).

``package_ready`` never transitions here: the effect is ``immutable_new_cycle`` — artifacts
stay frozen and the attorney starts the replacement cycle explicitly (``START_CYCLE``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.engine.orchestrator import machine
from app.engine.orchestrator.invalidation import INVALIDATION, Effect
from app.models.enums import DraftStatus, GateEvent, GateState
from app.models.orm import ArtifactSet, DemandDraft, Matter, StrategyPlan, User


@dataclass(frozen=True)
class RegistryBumpOutcome:
    """What one bump application did (or why it was a no-op)."""

    applied: bool  # False = idempotent no-op (cursor already covered the version)
    effect: Effect | None
    from_state: str
    to_state: str
    from_registry_version: int | None  # the locked cursor value (the audit's from)
    to_registry_version: int
    plans_invalidated: int
    drafts_superseded: int


def _lock_matter(db: Session, matter_id: uuid.UUID) -> Matter:
    """Row-lock + REFRESH the matter — the shared serialization point with gate actions.

    ``populate_existing`` matters: deciding the effect from an already-loaded object could
    overwrite a gate move that serialized first (SQLite ignores FOR UPDATE; the protocol is
    exercised for real on Postgres).
    """
    return db.execute(
        select(Matter)
        .where(Matter.id == matter_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def _invalidate_stale_plans(db: Session, *, matter: Matter, to_version: int) -> int:
    """Mark EVERY plan older than ``to_version`` stale (not just one latest/approved row)."""
    plans = db.scalars(
        select(StrategyPlan).where(
            StrategyPlan.matter_id == matter.id,
            StrategyPlan.registry_version < to_version,
            StrategyPlan.invalidated_by_registry_version.is_(None),
        )
    )
    count = 0
    for plan in plans:
        plan.invalidated_by_registry_version = to_version
        count += 1
    return count


def _supersede_stale_drafts(db: Session, *, matter: Matter, to_version: int) -> int:
    """SUPERSEDE every non-superseded draft older than ``to_version`` (existing status)."""
    drafts = db.scalars(
        select(DemandDraft).where(
            DemandDraft.matter_id == matter.id,
            DemandDraft.registry_version < to_version,
            DemandDraft.status != DraftStatus.SUPERSEDED.value,
        )
    )
    count = 0
    for draft in drafts:
        draft.status = DraftStatus.SUPERSEDED.value
        count += 1
    return count


def apply_registry_bump(
    db: Session,
    *,
    matter: Matter,
    user: User | None,
    to_registry_version: int,
) -> RegistryBumpOutcome:
    """Apply the invalidation matrix for a bump to ``to_registry_version`` — atomically.

    Locks + refreshes the matter first; returns an idempotent no-op when the locked cursor
    already covers the target version. Otherwise: invalidate stale plans/drafts BEFORE the
    gate moves, apply the machine's ``REGISTRY_BUMPED`` edge (``package_ready`` records
    ``immutable_new_cycle`` with NO transition — ``machine.advance`` would refuse), write
    the audit row (its ``from_registry_version`` is the LOCKED cursor, never a
    caller-supplied value), advance the cursor, and commit — one transaction; on failure
    everything (cursor included) rolls back so a retry re-attempts the invalidation.
    """
    locked = _lock_matter(db, matter.id)
    cursor = locked.invalidation_applied_registry_version
    if cursor is not None and cursor >= to_registry_version:
        return RegistryBumpOutcome(
            applied=False,
            effect=None,
            from_state=locked.gate_state,
            to_state=locked.gate_state,
            from_registry_version=cursor,
            to_registry_version=to_registry_version,
            plans_invalidated=0,
            drafts_superseded=0,
        )

    state = GateState(locked.gate_state)
    effect = INVALIDATION[state]
    from_state = locked.gate_state

    try:
        plans_invalidated = 0
        drafts_superseded = 0
        if effect in (Effect.PLAN_STALE_BACK_TO_EVIDENCE, Effect.DRAFT_STALE_G3_BLOCKED):
            # Invalidate BEFORE moving the gate: an invalidated approval must never be
            # reusable even if the transition below fails and retries.
            plans_invalidated = _invalidate_stale_plans(
                db, matter=locked, to_version=to_registry_version
            )
            drafts_superseded = _supersede_stale_drafts(
                db, matter=locked, to_version=to_registry_version
            )

        if state is GateState.PACKAGE_READY:
            # Immutable: no transition (machine.advance raises for this pair by design).
            # The cursor still advances — the REQUIRED action is the explicit cycle start.
            to_state = locked.gate_state
        else:
            transition = machine.advance(state, GateEvent.REGISTRY_BUMPED)
            locked.gate_state = transition.to.value
            to_state = transition.to.value

        record_event(
            db,
            firm_id=locked.firm_id,
            actor_id=user.id if user is not None else None,
            event_kind="registry_bump_applied",
            payload={
                "matter_id": str(locked.id),
                "from_state": from_state,
                "to_state": to_state,
                "effect": effect.value,
                "from_registry_version": cursor,
                "to_registry_version": to_registry_version,
                "plans_invalidated": plans_invalidated,
                "drafts_superseded": drafts_superseded,
            },
        )
        locked.invalidation_applied_registry_version = to_registry_version
        db.commit()
    except BaseException:
        db.rollback()  # cursor stays behind -> the next retry re-attempts invalidation
        raise

    return RegistryBumpOutcome(
        applied=True,
        effect=effect,
        from_state=from_state,
        to_state=to_state,
        from_registry_version=cursor,
        to_registry_version=to_registry_version,
        plans_invalidated=plans_invalidated,
        drafts_superseded=drafts_superseded,
    )


def packaged_registry_version(db: Session, *, matter: Matter) -> int | None:
    """The registry version of the matter's LATEST artifact set, or ``None`` (nothing packaged).

    Feeds the ``registry_newer_than_packaged_draft`` cycle-start guard and the package view's
    ``registry_version_current`` flag.
    """
    sets = list(db.scalars(select(ArtifactSet).where(ArtifactSet.matter_id == matter.id)).unique())
    if not sets:
        return None
    return max(sets, key=lambda s: (s.registry_version, s.draft_version)).registry_version


def reconcile_matter_cursor(
    db: Session, *, matter: Matter, user: User | None = None
) -> RegistryBumpOutcome | None:
    """One-time reconciliation for a legacy NULL-cursor matter (never grandfathers).

    Scans the matter's current derived state: if any CURRENT (non-invalidated plan /
    non-superseded draft / latest artifact set) row is OLDER than the matter registry, the
    matter was left stale by the pre-fix behavior — apply the full bump service. Otherwise
    initialize the cursor directly to the current version. Returns the bump outcome when one
    was applied, else ``None``.
    """
    locked = _lock_matter(db, matter.id)
    if locked.invalidation_applied_registry_version is not None:
        db.rollback()  # release the lock; nothing to reconcile
        return None

    current = locked.registry_version
    stale = False
    plan_rows = list(db.scalars(select(StrategyPlan).where(StrategyPlan.matter_id == locked.id)))
    if plan_rows:
        top_plan = max(plan_rows, key=lambda p: p.version)
        if top_plan.invalidated_by_registry_version is None and top_plan.registry_version < current:
            stale = True
    draft_rows = list(db.scalars(select(DemandDraft).where(DemandDraft.matter_id == locked.id)))
    if draft_rows:
        top_draft = max(draft_rows, key=lambda d: d.version)
        if (
            top_draft.status != DraftStatus.SUPERSEDED.value
            and top_draft.registry_version < current
        ):
            stale = True
    packaged = packaged_registry_version(db, matter=locked)
    if packaged is not None and packaged < current:
        stale = True

    if stale:
        return apply_registry_bump(db, matter=locked, user=user, to_registry_version=current)

    locked.invalidation_applied_registry_version = current
    db.commit()
    return None
