"""Named, pure guard functions for gate transitions.

Each guard is a total function ``GuardContext -> GuardResult`` — no side effects, no I/O,
no reads of global state. The machine's transition table (``machine.TRANSITIONS``) refers
to guards *by name*; ``REGISTRY`` is the lookup and the import-time contract (machine.py
asserts every name it uses exists here — drift fails loud at import).

Design notes (01 §1 invariants, orchestrator_gates §5):
- **role_attorney** — legal sign-off is *personal*: admins do NOT bypass an attorney gate
  (invariant 8, role-gated sign-off). An admin acting on a G1/G1.5/G2.5/G3 approve is
  refused exactly like a paralegal.
- **high_severity_dispositioned_or_override** — passes clean when no high-severity flags
  are open, OR via an audited override (design D2 / invariant 9: ``requires_override`` is
  allowed-but-logged, never silent). ``code`` distinguishes ``clean`` vs ``override`` so
  the caller writes the override reason into the ``GateRecord``.
- **registry_version_match** — approvals bind to a registry version; a bump since approval
  is a hard block ("records changed since approval", flow_04 invalidation matrix).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.engine.orchestrator.machine import Transition


@dataclass(frozen=True)
class GuardContext:
    """Everything the guards need, gathered by the caller before evaluation.

    Pure data — the caller (M1 persistence/API wiring) is responsible for populating this
    from the matter, the actor, the fact registry, and the compliance panel. Guards never
    reach back for anything not on this object.
    """

    actor_role: UserRole | None
    deadlines_confirmed: bool
    budget_available: bool
    registry_version_pinned: int | None
    registry_version_current: int
    open_high_severity_flags: int
    override_reason: str | None
    blocking_findings: int
    # BUS-05: the registry version of the matter's latest PACKAGED artifact set (None when
    # nothing has been packaged) — feeds the cycle-start guard at package_ready.
    packaged_draft_registry_version: int | None = None


@dataclass(frozen=True)
class GuardResult:
    """Outcome of one guard. ``code`` is a stable, machine-branchable discriminator."""

    passed: bool
    code: str
    detail: str


def _role_attorney(ctx: GuardContext) -> GuardResult:
    """Actor must be an attorney. Admins do NOT bypass — sign-off is personal (inv 8)."""
    if ctx.actor_role == UserRole.ATTORNEY:
        return GuardResult(True, "attorney", "actor is an attorney")
    actor = ctx.actor_role.value if ctx.actor_role is not None else "none"
    return GuardResult(
        False,
        "role_not_attorney",
        f"attorney sign-off required; actor role is {actor} (admins do not bypass)",
    )


def _deadlines_confirmed(ctx: GuardContext) -> GuardResult:
    """SOL / notice-of-claim deadlines must be attorney-confirmed before leaving G1 (inv 4)."""
    if ctx.deadlines_confirmed:
        return GuardResult(True, "deadlines_confirmed", "deadlines confirmed")
    return GuardResult(
        False,
        "deadlines_unconfirmed",
        "SOL / notice-of-claim deadlines are not yet attorney-confirmed",
    )


def _budget_available(ctx: GuardContext) -> GuardResult:
    """Per-matter AI budget must have headroom before starting a metered run (inv 12)."""
    if ctx.budget_available:
        return GuardResult(True, "budget_available", "budget available")
    return GuardResult(False, "budget_exhausted", "per-matter AI budget is exhausted")


def _registry_version_match(ctx: GuardContext) -> GuardResult:
    """Pinned registry version must equal the current one (approvals bind to a version).

    Failing detail names both versions so the UI can render the exact delta
    ("records changed since approval", flow_04).
    """
    pinned = ctx.registry_version_pinned
    current = ctx.registry_version_current
    if pinned is not None and pinned == current:
        return GuardResult(True, "version_match", f"registry version {current} matches approval")
    if pinned is None:
        return GuardResult(
            False,
            "version_unpinned",
            f"no registry version pinned; current is {current}",
        )
    return GuardResult(
        False,
        "version_mismatch",
        f"records changed since approval: approved at v{pinned}, now v{current}",
    )


def _high_severity_dispositioned_or_override(ctx: GuardContext) -> GuardResult:
    """No open high-severity flags, OR an audited override reason is supplied.

    ``code == "clean"`` when nothing was open; ``code == "override"`` when the attorney
    proceeds over open flags with a reason — the caller records that into the GateRecord
    as an audited override (design D2 / invariant 9). An empty/blank reason does not count.
    """
    if ctx.open_high_severity_flags == 0:
        return GuardResult(True, "clean", "no open high-severity flags")
    if ctx.override_reason is not None and ctx.override_reason.strip():
        return GuardResult(
            True,
            "override",
            f"{ctx.open_high_severity_flags} high-severity flag(s) open; overridden with reason",
        )
    return GuardResult(
        False,
        "high_severity_open",
        f"{ctx.open_high_severity_flags} high-severity flag(s) require disposition or override",
    )


def _no_blocking_findings(ctx: GuardContext) -> GuardResult:
    """No blocking compliance findings may remain open at G3."""
    if ctx.blocking_findings == 0:
        return GuardResult(True, "no_blocking", "no blocking compliance findings")
    return GuardResult(
        False,
        "blocking_findings_open",
        f"{ctx.blocking_findings} blocking compliance finding(s) remain",
    )


def _registry_newer_than_packaged_draft(ctx: GuardContext) -> GuardResult:
    """The cycle-start guard (BUS-05): new records must exist beyond the packaged draft.

    Refuses when nothing was packaged (no artifact set — package_ready without one is a
    data defect, fail closed) and when the matter registry still equals the packaged
    version (nothing new arrived; there is nothing to re-cycle for).
    """
    packaged = ctx.packaged_draft_registry_version
    current = ctx.registry_version_current
    if packaged is None:
        return GuardResult(
            False,
            "no_packaged_draft",
            "no packaged artifact set exists to start a replacement cycle from",
        )
    if current > packaged:
        return GuardResult(
            True,
            "registry_newer",
            f"registry v{current} is newer than the packaged draft (v{packaged})",
        )
    return GuardResult(
        False,
        "registry_not_newer",
        f"no new records since packaging (registry v{current}, packaged v{packaged})",
    )


REGISTRY: Mapping[str, Callable[[GuardContext], GuardResult]] = {
    "role_attorney": _role_attorney,
    "deadlines_confirmed": _deadlines_confirmed,
    "budget_available": _budget_available,
    "registry_version_match": _registry_version_match,
    "high_severity_dispositioned_or_override": _high_severity_dispositioned_or_override,
    "no_blocking_findings": _no_blocking_findings,
    "registry_newer_than_packaged_draft": _registry_newer_than_packaged_draft,
}


def evaluate(transition: Transition, ctx: GuardContext) -> list[GuardResult]:
    """Evaluate all of ``transition``'s guards and return every FAILURE (not first-fail).

    Returning all failures lets the UI show every unmet condition at once (design:
    let-click + inline reasons, no gray-out). An empty list means the transition is
    permitted. Guard names are resolved through ``REGISTRY``; a name absent from the
    registry is a programming error (machine.py's import-time assert prevents it reaching
    here) and surfaces as a ``KeyError``.
    """
    failures: list[GuardResult] = []
    for name in transition.guards:
        result = REGISTRY[name](ctx)
        if not result.passed:
            failures.append(result)
    return failures
