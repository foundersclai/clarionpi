# app.engine.orchestrator

Backs [`system_contract.md`](../system_contract.md) invariants **1, 4, 8, 9, 12**.
Module path: `backend/app/engine/orchestrator`.
Design source: [`backlog/pi/components/orchestrator_gates.md`](../../backlog/pi/components/orchestrator_gates.md).

## Status

**Implemented @ M0 (partial).** The package exists with an `errors.py` seam and
the `GateState`/`GateEvent`/`GateAction` enums (`app/models/enums.py`). The full
transition table, guards, invalidation matrix, run coordination, and audit
writes land **M1–M5**. It is the M0 owner of the gate machine's *shape*.

## Responsibility

The **gate state machine + run coordination + audit sink**. Owns the ten-state
machine (`corpus_processing → … → package_ready`), transitions with
role/deadline/registry/budget **guards**, one `GateRecord` per action, background
coordination of the phase0/analysis/demand runs, the SSE channel, and the
**registry-version invalidation matrix** when facts change mid-flow.

**Not responsible for:** gate *payload content* (each owning component builds its
own payload); prompt assembly (`app.engine.brain2`); arithmetic (`app.money.ledger`);
token minting (`app.engine.tokenizer`); wire serialization (`app.api.view_models`).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `GateState` machine, `GateRecord` audit, run jobs, SSE channel | — |
| Owns | `StrategyPlan` as the G2.5 payload entity; approval pins `registry_version` | — |
| Consumes | rendered gate payloads | brain2 / compliance / money.ledger / risk / chronology |
| Consumes | `DeadlineCandidate[]` (G1 confirm guard) | app.rules.jurisdiction |
| Produces | gate transitions + `gate_ready` / `status` / `error` events | app.api.view_models |
| Produces | run start/advance signals | all engine components |

## Invariants enforced

- **[1]** Every artifact passes its gate; illegal `(state, event)` pairs are
  refused (typed `error`, no transition, state unchanged).
- **[4]** Leaving `facts_review` requires attorney-confirmed deadlines.
- **[8]** Role guards: attorney-only on G1/G1.5/G2.5/G3 approve; paralegal preps
  G2a. Actor role is **server-derived**, never client-asserted.
- **[9]** Every transition writes exactly one `GateRecord`; `requires_override`
  (logged + reason) vs `unavailable` (hard stop).
- **[12]** Budget guard runs before any run; `budget_warning` at 80%.

## Vocabulary

`GateState` (10 states) · `GateEvent` (transition triggers) · `GateAction`
(`approve`/`reject`/`edit`/`override`) · `GateRecord` · `RunJob`
(`phase0`/`analysis`/`demand`) · `OverrideMode` (`requires_override` vs
`unavailable`) · **invalidation matrix** (registry bump → stay / back-edge /
block / immutable) · `registry_version` pin (G2.5) and match check (G3).

## Change rule

A boundary change requiring a contract update: adding/removing a `GateState` or
`GateEvent`; changing a transition guard, the role required at an edge, or the
invalidation-matrix routing; changing what pins or checks `registry_version`;
changing the SSE vocabulary the channel emits; adding a new run kind. Update this
file **and** [`system_contract.md`](../system_contract.md) §1/4/8/9/12 in the same PR.
