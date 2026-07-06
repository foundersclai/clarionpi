# System Contract

This document is the product and architecture contract for **ClarionPI** — the
personal-injury demand-package copilot. It records the high-level invariants that
remain true unless the team intentionally redesigns the system.

It is not a roadmap and not a duplicate of the code. It is the reference an agent
or engineer checks before changing provenance boundaries, attorney authority,
gate sequencing, money arithmetic ownership, tenancy/PHI handling, token
behavior, or module ownership.

Seeded from [`backlog/pi/01_high_level_design.md` §1](../backlog/pi/01_high_level_design.md)
(the 14 design invariants). Per-module boundaries live in
[`docs/module_contracts/`](module_contracts/); the drift matrix that binds those
contracts to the filesystem is [`CONTRACTS.md`](../CONTRACTS.md).

> **Honesty over aspiration.** This is an M0 repo. Where an invariant is fully
> enforced today the entry says so; where enforcement is deferred to a later
> milestone the entry names the milestone. Do not read a stated invariant as a
> claim that the guard already exists — read the **Enforcement** line.

## Update Rule

This file changes only with an **ADR** (in [`docs/adr/`](adr/)) or **explicit
founder approval**. A PR that touches a contract surface below updates this file
**and** the affected [module contract](module_contracts/) in the **same PR** —
`make hub-check` gates the drift, and the boundary and its contract must never
land apart.

Update this document, or add an ADR that links back to it, when a change alters
any of these contract surfaces:

- the separation between extracted facts (`OfficeFacts`-analog corpus rows),
  human elections (attorney/paralegal inputs), and derived state (chronology,
  ledger, drafts, registry)
- attorney authority, role-gated sign-off, explicit confirmation, override
  (`requires_override`) vs hard stop (`unavailable`), or audit durability
- provenance boundaries: page anchors, the fact registry, the token namespaces
  (`[[FACT/AMT/CITE/EX_n]]`), or the wire discipline that keeps tokens off the frontend
- the gate state machine, its transition guards, the registry-version
  invalidation matrix, or stale-artifact handling
- deterministic-vs-LLM ownership: what money/date math is pure code vs what is a
  semantic LLM check
- the jurisdiction rules boundary (lawyer-audited YAML → typed decisions)
- public wire contracts: REST view-models, discriminated gate payloads, or the
  SSE event vocabulary
- PHI / BAA-envelope handling, tenancy scoping, or the per-matter cost meter and cap
- module ownership or cross-module import rules (see [`CONTRACTS.md`](../CONTRACTS.md))

Small internal refactors, private-helper extraction, formatting, and local
implementation cleanups do not require a system-contract edit unless they change
one of the surfaces above.

## Core Invariants

### 1. The Product Is A Gated Copilot

The system prepares; humans approve. No artifact leaves the system without
passing its gate. The system may automate extraction, analysis, retrieval,
drafting, and bounded correction, but strategic legal work crosses a gate
boundary only after attorney review. Bounded correction loops are allowed only
inside a phase for narrow structural/format repair — they must not silently
decide strategy, skip a gate, or retry until a proxy judge is satisfied.

- **Enforced (M0):** the gate machine and its transition guards live in
  `backend/app/engine/orchestrator` (see
  [app.engine.orchestrator](module_contracts/app.engine.orchestrator.md)); the
  ten states are `backend/app/models/enums.py::GateState`. Illegal `(state,
  event)` pairs are refused, never transitioned.
- **Deferred:** the drafting/compliance loop bodies (`engine.brain2`,
  `engine.compliance`) are package stubs — the section-regen and G3 approval
  guards land **M5**.

### 2. Provenance Or It Doesn't Ship

Every factual assertion in any output resolves to one or more `(document, page)`
anchors. Orphaned facts fail G3 — they render as a sentinel and log loudly, and
never reach the wire.

- **Enforced (M0):** the anchor type (`PageAnchor`) and the
  non-empty-anchor discipline are modeled in `backend/app/models/schemas.py`
  (`MedicalEncounter.anchors`, `BillingLine.anchor`, `FactToken.anchors`); the
  token registry lives in `backend/app/engine/tokenizer` (see
  [app.engine.tokenizer](module_contracts/app.engine.tokenizer.md)).
- **Deferred:** anchor-window validation at extraction, orphan→sentinel
  resolution, and the G3 hard block land **M2** (registry) → **M5** (compliance
  panel). Corpus ingest is the provenance floor and lands **M1**.

### 3. The LLM Never Does Arithmetic

Specials totals, wage loss, demand math, and date math (SOL, treatment gaps) are
pure code. LLM output references `[[AMT_*]]` tokens only; it never emits a number
it computed.

- **Enforced (M0):** `backend/app/money` is the **only** arithmetic home for
  `Money`; currency is integer cents everywhere (`Cents` alias in
  `schemas.py`, `matter_budget_default_cents` in `core/config.py`), floats are
  banned for currency by convention + `ruff`/`mypy` + review (see
  [app.money.ledger](module_contracts/app.money.ledger.md)). Date math ownership
  is `backend/app/rules`.
- **Deferred:** the ledger rollup functions and `[[AMT]]` emission land **M2**;
  `app/money` is a package stub today.

### 4. Deadlines Are Deterministic And Attorney-Confirmed

SOL and notice-of-claim dates come from the jurisdiction rules table; the
attorney confirms at G1; the deadline warning is non-dismissible until confirmed.

- **Enforced (M0):** the deadline model (`DeadlineCandidate` with
  `assumptions`, `verify_status`, `confirmed`) is in `schemas.py`; the rules
  boundary is `backend/app/rules` (see
  [app.rules.jurisdiction](module_contracts/app.rules.jurisdiction.md)); the
  G1 confirm guard is asserted by the orchestrator transition table.
- **Deferred:** the AZ YAML packs, the fail-loud loader, and the
  `HybridEngine` lookup land **M1–M2**; `app/rules` is a stub with a `packs/`
  data directory today.

### 5. Tokenize Or Omit

Anything the LLM could fabricate — provider names, diagnoses, dates of service,
dollar amounts, legal citations, exhibit references — enters prompts and leaves
drafts only as tokens resolved from the fact registry. Adverse facts are tokens
too; stance is metadata, not a separate channel. One per-matter namespace, four
kinds (`FACT/AMT/CITE/EX`).

- **Enforced (M0):** the `FactToken` model + token-kind/status/source enums
  (`TokenKind`, `TokenStatus`, `TokenSource`) are in place; the tokenizer package
  (`backend/app/engine/tokenizer`) is the declared sole minter (see
  [app.engine.tokenizer](module_contracts/app.engine.tokenizer.md)).
- **Deferred:** the tokenizer/renderer body, prompt-vs-render resolution, and
  full mint/resolve enforcement land **M2**; the drafter that consumes display
  forms (`engine.brain2`) lands **M5**.

### 6. Adverse Facts: Surface Always, Volunteer Never

Treatment gaps, priors, degenerative findings, and prior claims are always shown
to the attorney with a required disposition (`address_in_letter` /
`omit_with_rationale` / `need_more_records`). They never appear in the letter
without disposition = `address_in_letter`, and are never silently dropped.

- **Enforced (M0):** the risk-flag model (`RiskFlag` with `FlagKind`,
  `FlagSeverity`, `FlagDisposition`) is in `schemas.py`/`enums.py`; the
  high-severity-blocks-G2a guard is named in the orchestrator transition table.
- **Deferred:** risk-flag detection (`engine/brain1/risk`) and the
  no-volunteer drafter constraint + `undisposed_adverse` G3 block land **M4–M5**.

### 7. PHI Stays Inside The BAA Envelope

Every external egress (LLM, OCR, storage, email, error tracking) is on a
maintained BAA inventory. No PHI to non-BAA endpoints, including any client-side
analytics on matter pages.

- **Enforced (M0):** the tenancy substrate lives in `backend/app/core` —
  every firm-scoped table carries `firm_id` (`schemas.py`, the ORM), which is the
  isolation primitive the envelope is built on. Cross-cutting home is `app/core`
  (see [app.core.llm_telemetry](module_contracts/app.core.llm_telemetry.md) and
  [app.core.matter_budget](module_contracts/app.core.matter_budget.md), which
  both point at the shared substrate).
- **Deferred:** the checked-in BAA egress inventory, the object-store adapter,
  PHI-access audit logging, and the OCR-vendor wiring land **M1** (ingest) with
  the `app/core` auth/audit workstream.

### 8. Role-Gated Sign-Off

Paralegals prepare (chronology edits, picks, disposition prep); only attorneys
approve G1, G1.5, G2.5, G3 (paralegals may prep G2a, attorneys confirm). Enforced
server-side — a paralegal `POST`ing an attorney approval is refused regardless of
UI state.

- **Enforced (M0):** the role enum (`UserRole`) is in place; the per-edge role
  guards are declared in the orchestrator transition table and are the
  orchestrator's responsibility (see
  [app.engine.orchestrator](module_contracts/app.engine.orchestrator.md)).
- **Deferred:** the server-side role middleware at the wire boundary
  (`api.view_models`) and the authenticated `AuthContext` (`app/core` auth
  workstream) land **M3**.

### 9. Attorney Final + Auditable

Overrides are `requires_override` (allowed, logged with a reason) vs `unavailable`
(hard stop). Every gate action records actor, role, and payload hash.

- **Enforced (M0):** the audit row (`GateRecord` with `action`, `actor_role`,
  `payload_hash`, `override_reason`, `idempotency_key`) and the `GateAction` enum
  are in `schemas.py`/`enums.py`; server-derived actor identity is the
  orchestrator's contract.
- **Deferred:** the append-only `AuditEvent` sink, the write-fails-the-action
  transactionality, and override-mode enforcement land with the `app/core` audit
  workstream and the orchestrator body (**M3**).

### 10. Extracted Facts, Human Elections, And Derived State Stay Separate

Corpus extractions (what the records say), attorney/paralegal inputs (what humans
decided), and derived artifacts (chronology, ledger, drafts, registry) live in
distinct stores; derived state is always rebuildable from the first two.

- **Enforced (M0):** the model layer keeps these in separate schemas/tables —
  corpus rows (`CaseDocument`, `DocumentPage`, `MedicalEncounter`,
  `BillingLine`), human inputs (`StrategyInputs`, `RiskFlag.disposition`,
  `GateRecord`), derived (`FactToken`, `StrategyPlan`) — and the specials ledger
  is modeled as a **derived view** over `BillingLine`, never a persisted total.
- **Deferred:** the rebuild paths (ledger recompute, registry rebuild) land
  with `app/money` and `app/engine/tokenizer` (**M2**).

### 11. The UI Displays State; It Does Not Invent It

AI overlays exist only in view-models on the wire; frontend submissions never
echo overlays back. A missing upstream value stays visible as missing/empty
state, never a fabricated default. Nothing token-shaped reaches the frontend.

- **Enforced (M0):** the wire boundary is `backend/app/api` and is a leaf —
  **nothing imports `api/` except `main.py`** (see
  [app.api.view_models](module_contracts/app.api.view_models.md)); the SSE
  vocabulary (`SseEvent`) forbids internal-reasoning events by construction.
- **Deferred:** the view-model layer, the serializer token-scanner, the
  closed submit schemas (`extra="forbid"`), and the sentinel-substitution
  backstop land **M3**.

### 12. Per-Matter AI Cost Is Metered And Capped

From day 1, on by default. Every LLM provider call is metered into a per-matter
cost accumulator; the cap gate runs before an LLM-spend op; a `budget_warning`
SSE fires at 80%.

- **Enforced (M0):** the metering DTOs (`LlmCall` with `cost_cents`,
  `MatterBudget` with `cap_cents`/`spent_cents`/`warned`) and the default cap
  (`matter_budget_default_cents` in `core/config.py`) are in place; the single
  metered door and the cap gate are owned by `app/core` (see
  [app.core.llm_telemetry](module_contracts/app.core.llm_telemetry.md) = the
  ledger/single-door, and
  [app.core.matter_budget](module_contracts/app.core.matter_budget.md) = the
  caps/warnings gate). The `MeteredLLMClient` single-door principle is the
  invariant: no provider handle exists outside it.
- **Deferred:** the `llm_provider` metered client, the `LLM_CALL` ledger
  rows, the reserve-then-commit budget accounting, and the meter-completeness CI
  test land with the `app/core` telemetry body (**M0–M1**, in progress).

### 13. Semantic Checks Are LLM Checks; Deterministic Checks Are Code

No code-side normalizers or allowlists patch semantic LLM output — a wrong
semantic verdict is fixed in the prompt or gated, never regex-patched. Mechanical
checks (token membership, ledger-hash equality, anchor existence) are pure code.

- **Enforced (M0):** the ownership split is structural — `app/money` (arithmetic),
  `app/rules` (deadline/fault decisions), and the deterministic G3 checks
  (`engine.compliance`) are code; the semantic judge is the LLM. Model tiering
  (Opus strategist / Sonnet extractor+judge / Haiku classifier) is contract, per
  [01 §3](../backlog/pi/01_high_level_design.md).
- **Deferred:** the compliance panel that runs both families (`engine.compliance`)
  is a stub; it lands **M5**.

### 14. Silent Wrong Output Requires Diagnostics Before Fixes

For wrong output with no error in logs, add diagnostic logging **before** applying
a code fix; the log must confirm the hypothesis at the layer with durable case
context (matter id, document/page, registry version, gate, token ids, ledger
hash). Per-matter run logs (ingest / extraction / rules / drafting) are written
for every phase; debugging starts from logs.

- **Enforced (M0):** this is a standing engineering discipline (see
  [`docs/debugging-policy.md`](debugging-policy.md)); the per-matter
  run-log sink is an `app/core` responsibility.
- **Deferred:** the `AgentRunLogger`-analog dual-emit (root logger + per-matter
  files) lands with the `app/core` telemetry/logging workstream (**M1**).

## Contract Change Workflow

ClarionPI uses **contract-first change**: a boundary change and its contract land
together, and `hub-check` gates the drift.

1. **Verify the live code and tests before claiming drift.** A stated invariant
   here may be `Deferred` — check the code, not the aspiration.
2. **Classify** each suspected drift as real, resolved, non-blocking, or unknown.
3. **If a contract surface changed intentionally,** update this document (or add
   an ADR in [`docs/adr/`](adr/) that links back) **in the same PR** as the code.
4. **If module ownership or a boundary moved,** update the relevant
   [module contract](module_contracts/) **and** the [`CONTRACTS.md`](../CONTRACTS.md)
   drift-matrix row in the same PR.
5. **Add or update the tests** that prove the contract for the touched path and
   any fallback path.
6. **`make verify`** (which runs `make hub-check`) must be green before the PR
   merges.
