# ADR-0005: M3 Wave B gate-action service decisions

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** foundersclai

## Context

M3 Wave B lands the gate-action service (`app/engine/orchestrator/service.py`) and the gates wire
(`app/api/routes/gates.py`) on top of the M0 pure gate machine + guards. The service is the single
door every human gate action goes through: it gathers guard context from the DB, applies attorney
edits, evaluates legality, runs registered side-effects, and writes the `GateRecord` + audit mirror
— all inside one transaction. Auth + role guards (Wave A) are recorded in
[ADR-0004](0004-m3-auth-decisions.md); this ADR records the five service-shape decisions that are
expensive to reverse or that set a boundary M4/M5 build on. Each keeps M3 shippable and
offline-testable while naming the heavier decision it defers.

## Decision

We adopt the following five decisions for the M3 Wave B gate-action service.

1. **Deadline confirmation is per-candidate (invariant 4 made structural).** Each
   `DeadlineCandidate.confirmed` flag is the attorney's G1 act; a G1 approve requires EVERY
   candidate on `matter.sol_candidates` to be `confirmed=True` (`deadlines_all_confirmed`). This is
   deliberately orthogonal to the candidate's `verify_status` (`RuleVerifyStatus` = verified /
   unverified), which is the lawyer-audit status of the rule *text* — confirming a deadline does not
   verify the underlying statute, and vice versa. An **empty** candidate list is NOT confirmed: a
   matter with no computed deadlines must not slide through G1. *Rollback:* relax
   `deadlines_all_confirmed` to a matter-level flag if per-candidate confirmation proves too
   granular for the pilot.
2. **`payload_version = matter.registry_version + count(GateRecords for the matter)`.** Both terms
   are monotonic (registry versions only bump; gate records are append-only), so the sum strictly
   increases on every state-changing act — an optimistic-concurrency fence with **no schema change**.
   The GET envelope exposes it; the submit echoes it; a stale value is refused `409`
   `stale_payload_version` carrying the fresh version (the FE refetch signal). *Rollback:* add a
   dedicated monotonic `payload_version` column on `Matter` if the derived sum ever needs to advance
   independently of registry/record counts.
3. **Idempotency key is client-minted, unique per matter (re-keyed from M0).** Migration 0005
   drops `gate` from M0's `(matter_id, gate, idempotency_key)` unique constraint, leaving
   `(matter_id, idempotency_key)` — a duplicate key anywhere on the matter replays the first
   outcome (the stored `GateRecord`) with the CURRENT matter state, writing no new record. A
   consequence of the pinned step order: a duplicate of an approve that already transitioned
   re-arrives addressed to the OLD gate and is answered by the gate-state-mismatch `409` (refetch),
   NOT a replay — replay serves only duplicates that did not move the state (edit/reject retries,
   same-state races). *Rollback:* restore `gate` to the unique constraint (downgrade path exists in
   0005) if per-gate replay is ever needed.
4. **Side-effects run in-transaction via a per-(state, event) registry.** `_SIDE_EFFECTS` maps a
   `(GateState, GateEvent)` to a callable that runs inside the action's transaction, after guards
   pass and before the state moves — an approve that fails its side-effect fails whole. G2a confirm
   freezes the matter's `RegistryVersion` row now (`_freeze_registry_version`); G2.5 approve is a
   registered documented **no-op** (`_pin_plan_version_noop`) whose M5 body pins the `StrategyPlan`
   version, and G3's package-kick is likewise reserved for M5 — the map names every gate with a
   post-approve effect so M5 replaces bodies without touching the dispatch. *Rollback:* inline a
   single side-effect at its call site if the registry indirection is not worth it.
5. **`rule_id == statute_cite` until rule-pack rows carry synthetic ids.** A `DeadlineConfirmation`
   identifies its candidate by `rule_id`, matched against the candidate's `statute_cite` — the only
   stable identifier a `DeadlineCandidate` carries today (lawyer-audited rule-pack rows have no
   synthetic ids). **Limitation:** two candidates that share a statute cite cannot be confirmed
   independently. Trigger to revisit: when the rules layer mints per-row synthetic ids (or a matter
   legitimately produces two candidates under one cite). *Rollback:* add a synthetic `rule_id` to
   `DeadlineCandidate` + the rule-pack rows and switch the match key, leaving `statute_cite` as
   display only.

## Consequences

- The gate-action path is end-to-end runnable and testable offline at M3: the service commits edits
  + record + side-effects + state move in one transaction, and any typed refusal rolls the whole
  action back (no partial edits on a refused approve).
- Each decision names its later counterpart (matter-level confirm fallback, dedicated version
  column, per-gate replay, inlined side-effects, synthetic rule ids) so the deferral is traceable,
  not silent.
- The re-key (decision 3) is a table rebuild under SQLite (`batch_alter_table` in migration 0005);
  the model/migration-drift test reflects a DB built by 0001..0005 and asserts it equals
  `Base.metadata`, so the constraint swap is locked.
- No SSE journal / `Last-Event-ID` replay ships in this wave — the gates wire is request/response;
  the streaming replay lands with the analysis/demand streams (M4/M5).

## Alternatives Considered

- **Matter-level deadline confirmation** — rejected: it would let a single flag stand in for
  per-candidate attorney review, weakening invariant 4. *Rollback:* above (1).
- **A dedicated `payload_version` column** — rejected for M3: the `registry_version + record count`
  sum is already monotonic and needs no migration. *Rollback:* above (2).
- **Keep `gate` in the idempotency unique constraint** — rejected: M3 pins idempotency as unique
  per matter, so a duplicate key must collide regardless of which gate it targeted. *Rollback:*
  above (3).
- **Side-effects after the commit (post-transaction hook)** — rejected: an approve that transitions
  but then fails its freeze would leave the registry unpinned behind a moved gate; the freeze must
  be atomic with the transition. *Rollback:* above (4).
- **Synthetic `rule_id` on candidates now** — rejected: the rule-pack rows have no id to derive one
  from at M3, so `statute_cite` is the honest identifier until they do. *Rollback:* above (5).
