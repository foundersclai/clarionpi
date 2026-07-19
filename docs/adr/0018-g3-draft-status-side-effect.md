# ADR-0018 — G3 approve marks the current draft APPROVED (a denorm side effect)

Status: accepted · Date: 2026-07-19 · Source: WD-2, workshop demo track
(`backlog/planned/workshop_demo_wd2_g3_draft_approval.md`)

> ADR numbers 0013–0017 are reserved by the S1 charter plan-set; WD-2 takes the next free
> number (0018).

## Context

`DraftStatus.APPROVED` was defined and read exactly once (`view_models.package_vm.buildable`,
`draft.status == APPROVED`) but never assigned: the `(COMPLIANCE_REVIEW, G3_APPROVED)`
transition ran no side effect (`orchestrator/service.py::_SIDE_EFFECTS` had no G3 entry), so a
compliance-passed draft stayed `IN_COMPLIANCE` after G3 approve. Consequence: `buildable` was
permanently `False` and the package-card showed a misleading "the draft is not approved yet —
building will refuse" hint on a matter whose build actually succeeds. The build route fences on
`gate_state == package_assembly` (`routes/drafting.py`), never on draft status — so this was a
UI-truth/credibility defect, not a build-reachability defect.

## Decision — G3 approve marks the current draft APPROVED, inside the gate transaction

Register `(COMPLIANCE_REVIEW, G3_APPROVED) → _approve_draft` in
`orchestrator/service.py::_SIDE_EFFECTS` (the per-`(state, event)` in-transaction registry). A
change to the side-effect registry is a boundary change under this module's contract Change
rule — hence this ADR plus the orchestrator/view-models/system-contract updates.
`_approve_draft` sets `latest_draft(...).status = DraftStatus.APPROVED.value` inside the action's
ONE transaction (design D4), symmetric with `_approve_plan_version`'s `plan.approved` denorm.

- **The GateRecord remains the authoritative approval trail.** `draft.status = APPROVED` is a
  draft-row DENORM of that fact, never a second source of truth. `DemandDraft` gains NO
  `approved_by`/`approved_at` columns — no schema change, no migration; the GateRecord carries
  the actor/time.
- **Build authorization is unchanged.** The build route still fences on `gate_state`, never on
  draft status; `buildable` stays a FE hint, never a build gate. The slice is additive.
- **Fail-loud, never silent.** `_approve_draft` raises `DraftMissing` (a `GuardRefused` subclass →
  the existing `409 guard_failed {guard: "demand_draft", code: "draft_missing"}` body, no new
  status or shape branch) when no current draft exists, rolling the whole action back. Unreachable
  on the normal path (`COMPLIANCE_REVIEW` is post-DRAFTING; `registry_version_match` blocks a
  superseded draft) — a silent skip was the exact bug. A fail-visible `clarionpi.orchestrator`
  ERROR fires if a future regression drops the map entry (dead on the normal path).
- **Cascade unchanged.** An APPROVED draft supersedes exactly like `IN_COMPLIANCE`/`VALIDATED` on
  a registry bump (`registry_bump.py::_supersede_stale_drafts`, status `!= SUPERSEDED`), and the
  `(PACKAGE_ASSEMBLY, REGISTRY_BUMPED) → EVIDENCE_REVIEW` back-edge (ADR-0012) still applies:
  `latest_draft` returns `None` and `buildable` falls back to `False`.

## Consequences

`buildable` and the package-card hint now tell the truth after a G3 approve. No schema/migration,
no new route or wire field (one new refusal code inside the existing 409 shape), no FE
production-code change, no LLM/provider call. Pre-production: there are no durable matters to
backfill — a matter already at/after `package_assembly` keeps its `IN_COMPLIANCE` draft and reads
`buildable = False` until a fresh cycle, which is correct (it was never marked approved).
