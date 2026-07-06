# app.core.matter_budget

Backs [`system_contract.md`](../system_contract.md) invariant **12 — Per-Matter AI
Cost Is Metered And Capped** (the **caps / warnings gate** half; the ledger &
single door are [app.core.llm_telemetry](app.core.llm_telemetry.md)).
Module path: `backend/app/core`.
Design source: [`backlog/pi/components/platform_core.md`](../../backlog/pi/components/platform_core.md).

## Status

**Implemented @ M0 (partial).** `app/core` exists; the `MatterBudget` DTO
(`cap_cents`, `spent_cents`, `warned`) is modeled in `app/models/schemas.py` and
the default cap (`matter_budget_default_cents`) is in `config.py`. The gate
function, the reserve-then-commit accounting, and the 80%-warning latch land
**M0–M1**.

## Responsibility

Owns the **per-matter cost gate and budget policy**. Runs a cap check **before**
an LLM-spend op and refuses (typed error → HTTP 402, surfaced by the orchestrator,
never a silent stall) once cumulative spend crosses the cap. Uses
**reserve-then-commit** accounting so concurrent runs on one matter cannot race
past the cap. Emits the `budget_warning` signal at **80%** (idempotent latch).
Caps are **ON by default**.

**Not responsible for:** the accumulator + per-call cost — it only **reads**
`app.core.llm_telemetry`, never mutates it; pricing; session ownership / auth /
per-org tier resolution (the auth workstream supplies the real tier; until then a
default tier applies).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | the cap gate, budget policy, reserve-then-commit accounting, the 80% latch | — |
| Consumes | the per-matter cumulative cost | app.core.llm_telemetry (reads it) |
| Consumes | the default cap + config | app/core config |
| Produces | `budget_warning` (at 80%) + the hard-stop decision | app.api.view_models (SSE) · app.engine.orchestrator |
| Produces | typed cap-exceeded refusal | app.engine.orchestrator (route-entry + per-iteration guard) |

## Invariants enforced

- **[12]** The cap gate runs before any LLM-spend op and fails closed once
  cumulative spend ≥ cap; `budget_warning` fires once at 80%. Bounding
  granularity: a route-entry check (each gate-advancing call) + a per-iteration
  check inside the analysis loop bound one op's overshoot to a single LLM call.
  Caps are ON by default; metering + cost visibility are always on.

## Vocabulary

`MatterBudget` (`cap_cents`, `spent_cents`, `reserved_cents`, `hard_stop`,
`warned`) · **reserve-then-commit** (reserve an estimate before the call, commit
actuals after) · the 80% `budget_warning` idempotent latch · the cap-exceeded
typed refusal (→ 402).

## Cross-cutting (`app/core`)

`app/core` is also the home of **tenancy, append-only audit, and auth**. Every
firm-scoped table carries `firm_id`; a `scoped_session(firm_id)` helper injects
the tenancy predicate so cross-firm reads are prevented by construction
(invariant **7**, and the basis for role-gated sign-off, invariant **8**). Gate/
PHI-access/artifact/export actions write an append-only `AuditEvent`
transactionally — an audit-write failure **fails the action** (invariant **9**).
The BAA egress inventory and per-matter run-log sink (invariant **14**) live here
too. These surfaces are shared with
[app.core.llm_telemetry](app.core.llm_telemetry.md).

## Change rule

A boundary change requiring a contract update: changing the gate granularity, the
reserve-then-commit protocol, the cap default, or the 80% warning semantics;
adding a tier or override protocol; changing the typed refusal contract the
orchestrator surfaces. Update this file **and**
[`system_contract.md`](../system_contract.md) §12 (and §7/9/14 for the
cross-cutting substrate) in the same PR.
