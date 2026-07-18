# ADR-0014 — Requested-demand settlement and exact plan binding

Status: accepted · Date: 2026-07-18 · Source: Workshop MVP plan set (WMVP-00/S1)

Decision owner: Requested demand and settlement.

Dependency: accept ADR-0014 before settlement implementation.

## Context

Requested demand can be an attorney-entered value, a rules-derived value, or an unresolved
election. Downstream drafting must not infer which value won, reuse a stale token, or bind a draft
to whichever approved plan happens to be newest.

## Decision

Requested-demand authority is a versioned election tied to the exact strategy-input revision and
the actor/gate authority that accepted it. The election settles the requested-demand token and the
`StrategyPlan` records that exact election. Plan and token therefore share one durable authority
chain; neither is reconstructed from display text.

Every new `DemandDraft` binds the full approved `StrategyPlan(firm_id,matter_id,id,version)`
identity. A matter-local plan version or a plan UUID without firm/matter scope is not sufficient.
Revisions append history; they do not mutate an approved plan in place.

ADR-0013 owns the full tenant-key and reference inventory. This ADR owns only requested-demand
election, token settlement, plan authority, and exact draft-to-plan binding. Draft/finding history
and G3 authority remain with ADR-0016.

## Consequences

- Drafting can prove which human election and strategy plan authorized every requested demand.
- Replays and retries resolve the same election/version instead of reselecting current state.
- Downstream constraints use the ADR-0013 strategy/authority shapes and full tenant scope.
