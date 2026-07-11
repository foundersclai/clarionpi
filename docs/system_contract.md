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
- **Enforced (M5):** the drafting/compliance loop bodies are live and gated —
  `backend/app/engine/brain2` drafts only after the G2.5 plan approve (the
  `_approve_plan_version` side effect pins the plan), a section that fails deterministic
  validation twice **surfaces** (`surfaced_failed`) rather than looping, and the G3 approve
  guard (`no_blocking_findings`, fed by `compliance.open_blocking_count`) refuses a draft
  with any open blocking finding. Bounded correction (span-patch / single-section regen) is
  narrow structural repair with a mandatory re-verify, never a strategy decision.

### 2. Provenance Or It Doesn't Ship

Every factual assertion in any output resolves to one or more `(document, page)`
anchors. Orphaned facts fail G3 — they render as a sentinel and log loudly, and
never reach the wire.

- **Enforced (M1):** corpus ingest is the provenance floor and is live —
  `backend/app/corpus/ingest/pages.py` is the sole author of `DocumentPage`, and page
  immutability is enforced in code and tests: the `(document_id, page_no)` anchor is a
  unique constraint (`orm.py`), a re-OCR appends a new `PageText` version and only moves
  `active_text_id` (`append_text_version`, never touching `page.id`/`page_no`/`image_ref`),
  and the invariant is locked by a hypothesis property test
  (`tests/corpus/test_pages.py`) plus the M1-exit scale run
  (`tests/corpus/test_phase0_integration.py`). The anchor type (`PageAnchor`) and the
  non-empty-anchor discipline are modeled in `backend/app/models/schemas.py`
  (`MedicalEncounter.anchors`, `BillingLine.anchor`, `FactToken.anchors`); the
  token registry lives in `backend/app/engine/tokenizer` (see
  [app.engine.tokenizer](module_contracts/app.engine.tokenizer.md)).
- **Enforced (M2):** anchor-window validation at extraction is live —
  `backend/app/corpus/extraction/runner.py` rejects any emitted row whose cited page
  falls outside the window span that produced it (`_anchors_in_window`, counted in
  `anchors_rejected`, never persisted), and every persisted `MedicalEncounter` /
  `BillingLine` / `IncidentFacts` row carries a validated `(document, page,
  window_id)` anchor. `resolve_for_render` runs anchor integrity — a token anchored
  only on a dedup-superseded document resolves `unverified`, and an orphan resolves
  to the `SENTINEL` with a loud log.
- **Enforced (M5):** the orphan/dead-anchor **hard G3 block** is live — the compliance
  panel's `orphan_token` and `dead_anchor` deterministic checks
  (`backend/app/engine/compliance/checks.py`) are in `HARD_BLOCK_KINDS` (never overridable
  to ship), and the package builder's provenance report is the per-demand proof that every
  rendered fact resolves to a live `(doc, page)` anchor (Part 1's completeness property).
  `dead_anchor` adds the page-bounds probe the registry mint-time check lacks (an anchor page
  beyond `page_count`). A missing/superseded exhibit page fails the binder build
  (`BinderPageMissing`), not a silent gap at delivery.

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
- **Enforced (M2):** the specials-ledger rollup + demand math and `[[AMT]]`
  emission are live and pure — `backend/app/money/specials.py` computes
  category/grand `LedgerColumns`, the demand-basis total, and the `line_set_hash`
  with integer cents only (no I/O, no `datetime.now`); `assemble.py` is the sole
  DB-touching layer. The LLM never emits a computed number: `app.engine.tokenizer`
  mints `[[AMT]]` tokens from `amounts_for_registry`, storing the value + `ledger_hash`
  snapshot, and drift is caught by re-hashing at render (`resolve_for_render`
  `amt_mismatch`), never by mutating a stored value.
- **Enforced (M5):** the G3 panel re-verifies every `[[AMT_n]]` against the LIVE ledger
  hash — `compliance/checks.py::_check_amt_ledger_mismatch` re-hashes the current ledger and
  flags any token whose stored `ledger_hash` no longer matches (a billing edit that landed
  after render), an `amt_ledger_mismatch` hard block; a ledger it cannot load flags every AMT
  (fail-visible). Drafting stays arithmetic-free: the drafter writes `[[AMT_n]]` tokens only,
  the deterministic validator rejects any literal `$…` figure, and `prose_total_mismatch`
  catches a rendered dollar literal that matches no AMT display form. The letter renders each
  amount from its token's display form (`cents_to_display`) — asserted ledger-exact in the
  M5-exit E2E.

### 4. Deadlines Are Deterministic And Attorney-Confirmed

SOL and notice-of-claim dates come from the jurisdiction rules table; the
attorney confirms at G1; the deadline warning is non-dismissible until confirmed.

- **Enforced (M0):** the deadline model (`DeadlineCandidate` with
  `assumptions`, `verify_status`, `confirmed`) is in `schemas.py`; the rules
  boundary is `backend/app/rules` (see
  [app.rules.jurisdiction](module_contracts/app.rules.jurisdiction.md)); the
  G1 confirm guard is asserted by the orchestrator transition table.
- **Enforced (M3):** the confirm is live and **per-candidate** —
  `service.deadlines_all_confirmed` requires EVERY `matter.sol_candidates` entry
  `confirmed=True` (an empty list is not confirmed: a matter with no computed
  deadlines cannot slide through G1), and the G1 approve guard evaluates it
  server-side. `confirmed` is the attorney's G1 act; the candidate's
  `verify_status` is the orthogonal lawyer-audit status of the rule text
  (**ADR-0005** decision 1). A confirmation matches its candidate by
  `rule_id == statute_cite` — a documented M3 limitation (two candidates sharing a
  cite cannot be confirmed independently; ADR-0005 decision 5).
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
- **Enforced (M2):** the registry mint + resolution are live in
  `backend/app/engine/tokenizer/registry.py` — the **sole** minter of the four
  token kinds in one shared per-matter ordinal namespace (`sync_extracted_facts`
  for extracted facts, `mint_amounts` for ledger AMTs, `mint_attorney_fact`), and
  the two resolution modes are enforced: `resolve_for_prompt` exposes **only**
  `display_form` (Brain-2 never sees raw names/cites/amounts), `resolve_text_for_wire`
  asserts nothing token-shaped survives onto a wire, and a missing token resolves to
  the `SENTINEL` (`"[UNRESOLVED FACT]"`, deliberately not token-shaped).
- **Enforced (M5):** the drafter consumes display forms and emits tokens only —
  `backend/app/engine/brain2/drafter.py` builds the section contract with each allowed
  token's `resolve_for_prompt` display form (never a raw name/amount/cite), the deterministic
  validator (`validator.py`) rejects an unregistered/disallowed token and any literal `$…`
  figure, and `renderer.py` resolves the tokenized body to a preview with a per-token span
  and asserts NOTHING token-shaped survives. The four artifacts re-scan every string
  (`ArtifactTokenLeak`) so no token reaches a deliverable. The G2a version freeze landed at
  **M4** (`_freeze_registry_version`); the plan + draft bind to that frozen `registry_version`.

### 6. Adverse Facts: Surface Always, Volunteer Never

Treatment gaps, priors, degenerative findings, and prior claims are always shown
to the attorney with a required disposition (`address_in_letter` /
`omit_with_rationale` / `need_more_records`). They never appear in the letter
without disposition = `address_in_letter`, and are never silently dropped.

- **Enforced (M0):** the risk-flag model (`RiskFlag` with `FlagKind`,
  `FlagSeverity`, `FlagDisposition`) is in `schemas.py`/`enums.py`; the
  high-severity-blocks-G2a guard is named in the orchestrator transition table.
- **Enforced (M4):** risk-flag detection is live — `engine/brain1/risk.py`
  `run_risk_detectors` (composed at the `analysis_running -> evidence_review` build,
  `engine/brain1/analysis.py`) **always surfaces** every derived flag and never
  suppresses: the per-kind cap is a UI display bound, not applied in the engine, and
  a deterministic detector's finding is persisted regardless. High-severity
  disposition is **attorney-only, server-enforced** — `disposition_flag` refuses a
  non-attorney HIGH disposition (`HighSeverityDispositionForbidden`), which
  `PUT /api/flags/{id}/disposition` maps to a typed `403`. The G2a-confirm guard
  `high_severity_dispositioned_or_override` blocks the approve while a HIGH flag is
  open (`409 override_required`) unless proceeded over via an audited override (the
  `requires_override` path); `open_high_severity_count` is the single named predicate
  the guard reads.
- **Enforced (M5):** the **no-volunteer** drafter constraint is live —
  `backend/app/engine/brain2/constraints.py::build_hard_constraints` buckets each flag by
  disposition into the late-bound hard-constraint block: `address_in_letter` → "Address in the
  letter", while `omit_with_rationale` / `need_more_records` / an UNDISPOSITIONED adverse flag →
  "Never mention or allude to" (an undispositioned adverse is no-volunteer by the conservative
  default). The `undisposed_adverse` G3 block is live (`compliance/checks.py`,
  `HARD_BLOCK_KINDS`): any undispositioned adverse flag is one hard-block finding.

### 7. PHI Stays Inside The BAA Envelope

Every external egress (LLM, OCR, storage, email, error tracking) is on a
maintained BAA inventory. No PHI to non-BAA endpoints, including any client-side
analytics on matter pages.

- **Enforced (M1):** the tenancy substrate lives in `backend/app/core` —
  every firm-scoped table carries `firm_id` (`schemas.py`, the ORM), which is the
  isolation primitive the envelope is built on. The object store is now inside the local
  envelope: `app/core/storage.py` is the single sanctioned path to case blobs (traversal-safe
  relative keys; `local` backend only at M1), and OCR egress defaults to `none` — no PHI
  leaves the box for OCR until the S1 vendor (with its BAA) is wired. Cross-cutting home is
  `app/core` (see [app.core.llm_telemetry](module_contracts/app.core.llm_telemetry.md) and
  [app.core.matter_budget](module_contracts/app.core.matter_budget.md), which
  both point at the shared substrate).
- **Enforced (M6):** PHI byte-access is audited at the read surface — the provenance viewer
  serves case blobs through `app/api/routes/provenance.py::get_document_blob`, which writes a
  committed `phi_access` audit row (actor + `document_id` + `surface`) BEFORE the bytes leave
  (the byte read is the audited PHI event; the token/metadata lookup is deliberately unaudited),
  mirroring the M5 artifact-download precedent (see
  [ADR-0008](adr/0008-m6-provenance-decisions.md)). The bytes are app-served over an
  authenticated, tenant-scoped route (a cross-firm document 404s) — the `local` backend has no
  presign, so no PHI leaves the box unaudited.
- **Deferred:** the checked-in BAA egress inventory is still the gating document for **R2**;
  the S3/MinIO object-store backend (prod account) and the live OCR-vendor + LLM-provider
  wiring land with that BAA/vendor decision (**S1/S4**). A presigned direct-to-store egress
  would replace the M6 app-serve only if the presign issuance itself is audited (so the
  `phi_access` row still precedes egress) — ADR-0008 (1).

### 8. Role-Gated Sign-Off

Paralegals prepare (chronology edits, picks, disposition prep); only attorneys
approve G1, G1.5, G2.5, G3 (paralegals may prep G2a, attorneys confirm). Enforced
server-side — a paralegal `POST`ing an attorney approval is refused regardless of
UI state.

- **Enforced (M0):** the role enum (`UserRole`) is in place; the per-edge role
  guards are declared in the orchestrator transition table and are the
  orchestrator's responsibility (see
  [app.engine.orchestrator](module_contracts/app.engine.orchestrator.md)).
- **Enforced (M3):** the guard is live and server-side — `deps.require_role`
  admits only the listed roles at the wire (typed `403 role_forbidden` +
  `required` + `actual`, rendered inline, no gray-out), the gate-action service
  re-derives the actor role from the authenticated user onto `GateRecord.actor_role`
  (never client-asserted), and the attorney-only approve guards refuse a paralegal
  `POST` regardless of UI state. Auth is in-house session auth behind one
  `get_current_user` door (**ADR-0004**).
- **Enforced (M4) for G2a prep/confirm:** paralegals prep G2a — exhibit **picks**,
  **chronology overlays**, and **billing edits** are open to any firm member at
  `evidence_review`, and a paralegal may disposition **low/medium** risk flags. The
  **attorney-only** acts are server-enforced: a HIGH-flag disposition
  (`HighSeverityDispositionForbidden -> 403`) and the third-party-PHI disposition
  (`PhiDispositionForbidden -> 403`). The analysis run itself is authorized as a
  **derived computation** over already-approved inputs (any authenticated firm member
  may trigger it — the G1.5 approval that authorized it already crossed the gate), not
  a human gate act.
- **Enforced (M5) for the demand/package runs:** the drafting routes
  (`routes/drafting.py`) are behind `get_current_user` + a firm-scoped `get_tenant_session`
  (a cross-firm matter `404`s, never `403`), and each SSE run is fenced to its gate
  (`demand/generate` → `drafting`, `package/build` → `package_assembly`, else `409
  gate_state_mismatch`). Plan emit + demand generate authorize as derived computations over
  the approved plan (any firm member); the attorney-only acts are the G3 approve guard and
  the finding **disposition** (`FindingDispositionForbidden -> 403`). The `ARTIFACTS_BUILT`
  advance moves only through `machine.advance` in the run.
- **Enforced (auth-hardening audit, SEC-01/02/03/04):** production boots FAIL-CLOSED —
  `validate_runtime_settings` (checked at `app.main` module construction, so `--lifespan
  off` cannot bypass it, and again in the lifespan) refuses `APP_ENV=prod` without
  `AUTH_MODE=session`, an insecure session cookie, a disabled CSRF check, a non-HTTPS
  trusted-origin list, a placeholder throttle-HMAC secret, or an implicit trusted-proxy
  posture, and refuses unknown `APP_ENV`/`AUTH_MODE` values outright. The session cookie
  is HTTPS-only in prod (`Secure`, env-derived) rooted at `path=/`; every unsafe-method
  request in session mode must carry exactly one `Origin` header matching a configured
  trusted origin (`403 csrf_failed` otherwise — login/logout included). Login is
  throttled by INDEPENDENT account (canonical-email) and IP HMAC-keyed buckets —
  `429 login_throttled` + `Retry-After` when locked, uniform persistence for known and
  unknown emails (no existence oracle), forwarded-client identity honored only from
  configured proxy CIDRs with Uvicorn proxy parsing disabled everywhere
  (`--no-proxy-headers`, launch-config regression-tested), and the global
  `users.normalized_email` unique constraint making one canonical email one login
  principal (ADR-0010).
- **Deferred:** TOTP (second factor) is restated for **R2** (ADR-0004 decision 2).

### 9. Attorney Final + Auditable

Overrides are `requires_override` (allowed, logged with a reason) vs `unavailable`
(hard stop). Every gate action records actor, role, and payload hash.

- **Enforced (M0):** the audit row (`GateRecord` with `action`, `actor_role`,
  `payload_hash`, `override_reason`, `idempotency_key`) and the `GateAction` enum
  are in `schemas.py`/`enums.py`; server-derived actor identity is the
  orchestrator's contract.
- **Enforced (M3) for gate actions:** `service.apply_gate_action` writes exactly
  one `GateRecord` per action (actor + server-derived role + `payload_hash`) **and**
  a synchronous append-only `AuditEvent` mirror in the **same transaction** — an
  audit-write failure fails the action, and a refused action rolls back whole (no
  partial edits). An `override` requires a non-blank reason (`422`
  `override_reason_required`), recorded on the record (allowed-but-logged);
  `high_severity_open` surfaces as `409 override_required` vs a hard-stop
  `guard_failed` (**ADR-0005** decisions; ADR-0004 for the audit substrate).
- **Enforced (M5) for G2.5/G3 sign-off:** the G2.5 plan approve and the G3 compliance
  approve are in the audited-gate set — each writes a `GateRecord` + `AuditEvent` through
  `apply_gate_action`, the G2.5 side effect stamps `approved`/`approved_by`/`approved_at`
  on the plan, and a finding disposition writes its own `compliance_finding_dispositioned`
  audit (an OVERRIDE recorded with a reason, surfaced in the provenance report's judgment-call
  log). The G3 approve refuses a draft with any open blocking finding (`no_blocking_findings`);
  the package build writes an `artifact_set_built` + `package_ready` audit, and each artifact
  download is audited (`artifact_downloaded`).

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

- **Enforced (late-document audit, BUS-05/ADR-0012):** derived state can never stay
  SILENTLY stale — one bump owner (`orchestrator/registry_bump.py`) applies the flow_04
  matrix under the shared matter row lock (gate actions take the same lock first), marks
  stale plans (`invalidated_by_registry_version` — approval survives as history, never
  reusable) and supersedes stale drafts, and is driven by the durable
  `Matter.invalidation_applied_registry_version` cursor (crash-recoverable; legacy NULL
  cursors reconciled, never grandfathered). `package_assembly` cascades like drafting;
  demand/package completions re-lock and refuse stale advances (`draft_registry_drift`).
  EX tokens settle at G2a confirm (settle → cursor → freeze, one transaction; tokenizer
  runs caller-owned `commit=False`); the package build consumes settled tokens READ-ONLY
  and the manifest GET can never mint. `package_ready` is non-terminal: the attorney-only
  `start_cycle` action (guarded `registry_newer_than_packaged_draft`) re-enters
  `evidence_review` with every prior artifact byte/row untouched, and the package view
  carries explicit `registry_version_current` / `new_cycle_required` / per-set `current`.

### 11. The UI Displays State; It Does Not Invent It

AI overlays exist only in view-models on the wire; frontend submissions never
echo overlays back. A missing upstream value stays visible as missing/empty
state, never a fabricated default. Nothing token-shaped reaches the frontend.

- **Enforced (M0):** the wire boundary is `backend/app/api` and is a leaf —
  **nothing imports `api/` except `main.py`** (see
  [app.api.view_models](module_contracts/app.api.view_models.md)); the SSE
  vocabulary (`SseEvent`) forbids internal-reasoning events by construction.
- **Enforced (M3) at the gates wire:** responses are **view-models only**
  (`facts_review_vm` / `strategy_intake_vm` / `minimal_gate_vm` build JSON-safe
  dicts; a missing upstream value stays visible as missing/empty, never a
  fabricated default), submit schemas are **closed** (`extra="forbid"`, so an
  overlay field echoed on a submit is a `422`), and the token-scanner
  (`wire_guard.scan_wire_payload`) runs on every gate envelope — dev/test raise
  `TokenLeak` (500 + loud log), prod scrubs to the registry `SENTINEL` + logs
  `clarionpi.wire`. The prod sentinel backstop is deliberately not token-shaped.
- **Enforced (M5) at the drafting/package wire:** the M5 view-models
  (`plan_review_vm` / `compliance_review_vm` / `package_vm`) are wire-scanned like the
  rest — the compliance panel exposes each section's RENDERED preview + BARE-id spans,
  NEVER the tokenized `body_tokenized` (a `[[FACT_n]]` string would trip the scanner). The
  artifact serializer (`artifact_sets_view`) surfaces only `{kind, sha256, byte_count, url}`
  — the internal `object_key` never reaches the wire — and every artifact builder re-scans
  its strings before finalizing (`ArtifactTokenLeak` on a survivor), so no token reaches a
  deliverable (asserted in the M5-exit E2E: `letter.docx` has zero token matches).
- **Enforced (M6) at the provenance read surface:** the rendered-letter span map
  (span_id → fact_id) now reaches the FE viewer — the compliance panel's BARE-id
  `DraftSection.spans` click through to `get_token_provenance`, whose response
  (`{token_id, display_form, outcome, source, anchors[]}`, each anchor page-level with
  `bbox: null`) is wire-scanned like the rest, so nothing token-shaped escapes; the accepted
  path id is the BARE registry grammar only (`^(FACT|AMT|CITE|EX)_\d+$` → 422 otherwise), so
  no token-shaped string is even accepted on the request path (ADR-0008 §2–3).
- **Enforced (upload-safety audit, BUS-06/SEC-05) at the upload wire:** every
  `UploadSlotView` carries a stable `ordinal` (zero-based registration order, unique per
  session); the frontend pairs browser files to slots **by ordinal only** — response-array-
  index pairing is forbidden (it silently attached bytes to the wrong declared identity),
  and a slot ordinal with no matching file fails the upload mutation before commit. Uploads
  are bounded: registration and the streamed slot PUT enforce the configured limits with
  typed refusals — `413 upload_limit_exceeded` (+ `limit`) and `422 upload_size_mismatch` —
  and a slot is marked received only when actual bytes equal the declared size.
- **Deferred:** promoting the scanner to a response middleware; SSE `Last-Event-ID` replay is
  deferred too.

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
- **Enforced (M2):** the split is live at the extraction boundary — encounter
  merge (`backend/app/corpus/extraction/merge.py`) collapses exact-key duplicates by
  **rule** (casefold + whitespace on `(provider, date, encounter_type)`, no model
  call) and sends only genuine near-matches (same date, provider Jaccard ≥ 0.5) to
  the `merge_tiebreak` model; a pair the model can't reach is left **unmerged and
  counted**, never guessed in code. Ledger-hash equality and anchor existence
  (`app.money.specials`, `app.engine.tokenizer`) are pure-code mechanical checks.
- **Enforced (M5):** the compliance panel runs both families — `checks.py` owns the seven
  deterministic code predicates (token membership, live ledger-hash equality, anchor existence,
  page bounds), `judge.py` owns the three semantic verdicts (Sonnet). Neither side post-filters
  the other: a semantic finding is never regex-patched (a fix is a snapshot-neutral regen or an
  audited disposition), and a judge claiming a mechanical `check_kind` fails the
  `JudgeFindingBatch` schema. Drafter↔judge **snapshot symmetry** is load-bearing — the judge
  rebuilds the drafter's `DrafterPromptSnapshot` and re-hashes its `input_hash`; a mismatch
  fails the run loudly (`SnapshotDrift`), so it grades the drafted world, not a drifted one. A
  judge that cannot return a valid verdict emits a fail-visible BLOCKING marker, never a silent
  clean.

### 14. Silent Wrong Output Requires Diagnostics Before Fixes

For wrong output with no error in logs, add diagnostic logging **before** applying
a code fix; the log must confirm the hypothesis at the layer with durable case
context (matter id, document/page, registry version, gate, token ids, ledger
hash). Per-matter run logs (ingest / extraction / rules / drafting) are written
for every phase; debugging starts from logs.

- **Enforced (M1):** the per-matter run-log sink is live —
  `app/core/matter_logs.py::MatterRunLogger` dual-emits each JSON line to a
  `<logs_dir>/<matter_id>/<phase>.log` file **and** the root logger, and the ingest phase
  writes one (`run_started` → per-doc `doc_classified`/`doc_pages_built`/`doc_dedup` →
  `gate_advanced`/`late_documents_processed` → `run_completed`, plus `run_error` on an
  unexpected failure). Debugging a silent corpus problem starts from that file. This remains a
  standing engineering discipline (see [`docs/debugging-policy.md`](debugging-policy.md)).
- **Enforced (M4):** the analysis phase writes its own per-matter run log —
  `engine/brain1/analysis.py` runs under `MatterRunLogger(matter.id, "analysis")`
  (`run_started` → `registry_synced` / `chronology_built` / `ledger_amounts_minted` /
  `risk_flags_generated` → `gate_advanced` → `run_completed`, plus `run_error`), so a
  silent analysis problem starts from that file.
- **Enforced (M5):** the drafting phase writes its own per-matter run log —
  `engine/brain2/generate.py` runs under `MatterRunLogger(matter.id, "demand")`
  (`run_started` → `draft_created` / `memo_generated` → per-section
  `section_retry` / `section_passed` / `section_surfaced_failed` → `gate_advanced` /
  `draft_incomplete` → `run_completed`, plus the typed refusals), so a silent drafting
  problem starts from that file.
- **Deferred:** the rules phase run log lands with the `HybridEngine` lookup.

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
