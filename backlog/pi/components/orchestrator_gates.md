# Component — orchestrator_gates

- **Status:** DRAFT for founder review · **Date:** 2026-07-04
- **Planned module path:** `app/engine/orchestrator`
- **Contract doc (M0):** `docs/module_contracts/app.engine.orchestrator.md`
- Refines [04 §2 `GateRecord`](../04_data_model_and_contracts.md), [01 §4 gate machine](../01_high_level_design.md),
  [04 §4 SSE](../04_data_model_and_contracts.md).

## 1. Responsibility

The **gate state machine + run coordination + audit**. Owns transitions across the ten gate
states with role/deadline/registry/budget **guards**, writes a `GateRecord` for every action,
coordinates phase0/analysis/demand as background jobs, owns the SSE channel, and enforces the
**invalidation matrix** when the fact registry bumps mid-flow.

**NOT responsible for:** gate *payload content* (each component owns its payload); prompt
assembly (`brain2_drafting`); arithmetic (`money_engine`); token minting (`fact_registry`).

## 2. Boundary

| Direction | What | Peer component |
|---|---|---|
| consumes | gate payloads (rendered) from each owning component | chronology_builder / risk_flag_engine / money_engine / brain2_drafting / compliance_engine |
| consumes | `DeadlineCandidate[]` (G1 confirm guard) | jurisdiction_rules.md |
| owns | gate `GateState` machine, `GateRecord` audit, run jobs, SSE channel | — |
| owns | `StrategyPlan` (G2.5 payload entity — assembled from the jurisdiction_rules.md skeleton + brain2_drafting.md emphasis; approval binds `registry_version`) | — |
| produces | gate transitions + `gate_ready` / `status` / `error` events | api_and_wire.md |
| produces | run start/advance signals | all engine components |

## 3. Key types & fields

```python
class GateRecord:                          # extends 04 §2 — one per transition (inv. 9)
    matter_id: UUID; gate: str
    action: Literal["approve","reject","edit","override"]
    actor_id: UUID; actor_role: str        # server-derived, not client-asserted
    payload_hash: str; override_reason: str | None
    idempotency_key: str; created_at: datetime

class RunJob:                              # Procrastinate background job
    matter_id: UUID; kind: Literal["phase0","analysis","demand"]
    heartbeat_at: datetime; registry_version_at_start: int
    owns_sse_channel: bool

class OverrideMode(Enum):
    REQUIRES_OVERRIDE = "allowed_logged"   # proceed + GateRecord(override) + reason
    UNAVAILABLE = "hard_stop"              # cannot proceed
```

## 4. Internal design

### Transition table (state × event → state, with GUARDS)

| From | Event | To | Guard |
|---|---|---|---|
| `corpus_processing` | corpus ready | `facts_review` | — |
| `facts_review` | G1 confirm | `strategy_intake` | attorney role **+ deadline-confirm** (inv. 4) |
| `strategy_intake` | G1.5 submit | `analysis_running` | attorney role + **budget guard (inv. 12 — precheck BEFORE the run enqueues, per flow_01/03)** |
| `analysis_running` | analysis ready | `evidence_review` | — |
| `evidence_review` | picks changed / records added | `analysis_running` | — (re-run) |
| `evidence_review` | G2a confirm | `plan_review` | attorney confirm (paralegal prep only) (inv. 8); all high-severity flags dispositioned |
| `plan_review` | strategy revised | `strategy_intake` | attorney role |
| `plan_review` | G2.5 approve | `drafting` | attorney role + budget guard; **pins `registry_version`** |
| `drafting` | draft complete | `compliance_review` | — |
| `compliance_review` | semantic finding | `drafting` | — (section regen) |
| `compliance_review` | G3 approve | `package_assembly` | attorney role; **registry_version match** (hard block on mismatch) |
| `package_assembly` | artifacts built | `package_ready` | — |
| `package_ready` | — | *(immutable)* | new version = new draft cycle |

*Illegal (state, event) pairs return a typed `error`, write no transition, and leave state
unchanged.* Role is **server-derived** (invariant 8) — a paralegal may prep G2a but the
confirm event is rejected without an attorney actor.

### Invalidation matrix (registry bump while in-flight)

| Registry bumps while in… | Effect | Route |
|---|---|---|
| `evidence_review` | evidence stale | **stay**, re-present the gate at new version |
| `plan_review` | plan stale | back-edge → `evidence_review` (re-confirm evidence first) |
| `drafting` / `compliance_review` | draft stale, **G3 blocked** | route → `evidence_review` re-confirm |
| `package_ready` | immutable | no in-place change — a new version starts a fresh draft cycle |

Approvals **bind to `registry_version`** ([04 §2](../04_data_model_and_contracts.md) invariant 3):
G2.5 pins it; G3 hard-blocks on mismatch ("records changed since plan approval"). Drift
invalidates downstream approvals **explicitly, never silently** ([01 §9](../01_high_level_design.md)).

### Run coordination & currentStep discipline

- phase0/analysis/demand run as **Procrastinate** background jobs; the orchestrator owns the
  single SSE channel and emits only the [04 §4](../04_data_model_and_contracts.md) vocabulary
  (`status`, `gate_ready`, `error`, `budget_warning` at 80%) — **no internal-reasoning events**.
- **currentStep stays on the owning gate for the whole stream**; the FE keys on `isRunning`,
  not step churn (TM `currentStep`/`isResearching` lesson).
- **Re-entrancy:** late documents re-enter `corpus_processing` without losing gate context;
  the invalidation matrix decides the downstream consequence.

## 5. Invariants enforced

- **1** — every artifact passes its gate; illegal transitions are refused.
- **4** — leaving `facts_review` requires confirmed deadlines.
- **8** — role guards: attorney-only on G1/G1.5/G2.5/G3 approve; paralegal preps G2a.
- **9** — every transition writes a `GateRecord`; `requires_override` (logged) vs `unavailable`
  (hard stop).
- **12** — budget guard before any run; `budget_warning` at 80%.

## 6. Failure modes & handling

| Failure | Detection | Handling |
|---|---|---|
| Orphaned run (worker died) | heartbeat stale beyond threshold | Stale-run reaper marks failed → `error`; matter re-runnable |
| Double-submit of a gate action | duplicate `idempotency_key` | Second submit is a no-op returning the first result |
| Concurrent attorney/paralegal edits | optimistic-lock version clash | Reject the losing write; surface conflict, don't merge |
| G3 approve on stale registry | `registry_version` mismatch | Hard block; route per invalidation matrix |
| Missing GateRecord for a transition | audit-completeness check | Treated as a bug; transition rejected if the record can't be written |

## 7. Test strategy

- **Transition-table exhaustive grid:** every (state × event) including all illegal pairs →
  expected next state or typed `error`; role guards asserted at each approval edge.
- **Invalidation-matrix fixtures:** bump registry in each in-flight state → asserted stay /
  back-edge / block / immutable behavior.
- **Audit completeness:** every legal transition produces exactly one `GateRecord` with actor,
  role, payload_hash; overrides carry a reason.
- **Concurrency:** double-submit idempotency; optimistic-lock conflict surfaced not merged;
  stale-run reaper fires on a stalled heartbeat.

## 8. Open questions

1. Stale-run heartbeat threshold vs the longest legitimate phase0 (large OCR batches) — one
   global timeout, or per-job-kind? (Tune against real corpus sizes.)
2. On a `plan_review` back-edge to `evidence_review`, does G1.5 `StrategyInputs` survive
   untouched, or re-confirm? (Leaning survive — inputs are attorney signal, not derived state.)
3. Does `need_more_records` (from risk_flag_engine) hold G2.5, or advance with an open item
   tracked on the matter? (Coordinate with re-entrancy path.)
