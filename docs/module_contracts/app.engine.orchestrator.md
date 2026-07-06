# app.engine.orchestrator

Backs [`system_contract.md`](../system_contract.md) invariants **1, 4, 8, 9, 12**.
Module path: `backend/app/engine/orchestrator`.
Design source: [`backlog/pi/components/orchestrator_gates.md`](../../backlog/pi/components/orchestrator_gates.md).

## Status

**Implemented @ M3 (service layer live).** The pure gate machine, guards, and
registry-version invalidation matrix landed at **M0** (`machine.py`, `guards.py`,
the `GateState`/`GateEvent`/`GateAction` enums). M3 Wave B adds the **gate-action
service** (`service.py::apply_gate_action`) + the gates wire
(`app/api/routes/gates.py`): the single door that gathers guard context from the
DB, applies attorney edits, evaluates legality, runs registered side-effects, and
writes one `GateRecord` + the audit mirror — all in **one transaction** (a refused
action rolls back whole). The five pinned service decisions are recorded in
[ADR-0005](../adr/0005-m3-gate-service-decisions.md).

**Still lands M4/M5:** the analysis/demand run coordination + SSE channel and the
Procrastinate (background-job) decision (M4); the G2.5 plan-version pin and the G3
package-kick side-effects (M5 — registered now as documented no-ops).

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
  refused (`IllegalGateAction` → `409`, no transition, state unchanged).
- **[4]** Leaving `facts_review` requires EVERY `DeadlineCandidate` confirmed
  (`deadlines_all_confirmed`; an empty candidate list is not confirmed).
- **[8]** Role guards: attorney-only on G1/G1.5/G2.5/G3 approve; paralegal preps
  G2a. Actor role is **server-derived** onto `GateRecord.actor_role`, never
  client-asserted.
- **[9]** Every action writes exactly one `GateRecord` + a synchronous audit
  mirror in the same transaction; `override` requires a non-blank reason
  (`OverrideReasonRequired` → `422`); `high_severity_open` → `OverrideRequired`
  (`409`) vs a hard-stop guard failure (`GuardRefused` → `409`).
- **[12]** Budget guard runs in the approve guard context (spend strictly under
  cap); `budget_warning` at 80% (surfaced from `app.core.matter_budget`).

## Vocabulary

`GateState` (10 states) · `GateEvent` (transition triggers) · `GateAction`
(`approve`/`reject`/`edit`/`override`) · `GateRecord` · `RunJob`
(`phase0`/`analysis`/`demand`) · `OverrideMode` (`requires_override` vs
`unavailable`) · **invalidation matrix** (registry bump → stay / back-edge /
block / immutable) · `registry_version` pin (G2.5) and match check (G3).

**Service surface (M3):** `apply_gate_action` (the single door) →
`GateActionResult` (`transitioned`, `from_state`, `to_state`, `replayed`) ·
`GATE_EVENT_BY_APPROVE` (the ONLY five human-approvable gates → their approve
event) · `payload_version` (= `registry_version + GateRecord count`, monotonic
fence, no schema change) · client-minted `idempotency_key` (unique per matter;
duplicate **replays** the first outcome with the current state) ·
`dry_run_approve_blockers` (side-effect-free guard preview for the wire) ·
`_SIDE_EFFECTS` (per-`(state, event)` in-transaction callables; G2a freezes the
`RegistryVersion`, G2.5/G3 reserved for M5). **Typed refusals → HTTP:**
`GateStateMismatch`/`StalePayloadVersion`/`IllegalGateAction`/`OverrideRequired`
→ `409`; `GuardRefused` → `409` (except `role_attorney` → `403 role_forbidden`);
`OverrideReasonRequired`/`UnknownDeadlineRule`/`EditsNotSupported`/`InvalidEdits`/
`InvalidIdempotencyKey` → `422`.

## Change rule

A boundary change requiring a contract update: adding/removing a `GateState` or
`GateEvent`; changing a transition guard, the role required at an edge, or the
invalidation-matrix routing; changing what pins or checks `registry_version`;
changing the SSE vocabulary the channel emits; adding a new run kind; changing the
service surface (the `apply_gate_action` step order, `GATE_EVENT_BY_APPROVE`, the
`payload_version` formula, the idempotency/replay semantics, the typed-refusal →
HTTP mapping, or the side-effect registry). A change to any of these lands with a
new ADR (cf. [ADR-0005](../adr/0005-m3-gate-service-decisions.md)). Update this
file **and** [`system_contract.md`](../system_contract.md) §1/4/8/9/12 in the same PR.
