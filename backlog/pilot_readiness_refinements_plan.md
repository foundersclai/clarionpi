# Pilot-Readiness Refinements — Preflight, G2a Checklist, Package-Review Gate, Workbench Consolidation

Status: DRAFT (pending review-fix loop)
Date: 2026-07-10
Source: external workflow review (2026-07-10) + `docs/audit/` static audit (2026-07-06),
assessed against the as-built code. Architecture verdict from both: keep the evidence
spine / gates / frozen registry / tokens-only drafting; refine the attorney experience
and close the final-artifact approval gap.

## Context

Five refinements were proposed: (1) intake preflight, (2) exception-driven G1,
(3) G1+G1.5 visual merge, (4) G2a "Ready to Demand?" checklist, (5) final artifact
approval after package assembly — plus (6) a deliberately restricted pilot box
(adult AZ private-party MVA, ordinary open demand; no public entities, minors,
wrongful death, time-limited demands, or coverage disputes).

Assessment against the code found the box is already ~80% enforced by existing scope
(`ClaimType.MVA` is the sole claim type, `backend/app/models/enums.py:386`;
jurisdiction refusal at `backend/app/api/routes/matters.py:55`;
`demand_type: Literal["open"]` at `backend/app/models/schemas.py:547`), `Matter`
already carries `incident_date` and `sol_candidates` (`backend/app/models/orm.py`),
and the artifact layer already versions rebuilds
(`backend/app/package/build.py:116`) — so the real work is smaller and sharper than
the review implied.

## Prerequisite queue (not part of this plan)

> **STATUS 2026-07-11: the queue is SHIPPED** (order 02 → 01 → 03 → 04 → 05, commits
> `5326d3f..` on main; ADRs 0010–0012; migrations 0010–0013). ADR-0009 numbering stays
> RESERVED for WI-1's package_review design, which remains ON HOLD per founder
> direction; WI-3's attestation wording remains with the legal cofounder.

`docs/audit/plans/01–05` ship FIRST. They are the launch blockers this plan builds on:

1. `02-upload-safety-and-slot-correctness` (SEC-05 + BUS-06) — **highest urgency**:
   BUS-06 (FE maps files to upload slots by array index; backend order not
   guaranteed) can attach wrong bytes to a document identity and corrupt the
   provenance spine.
2. `01-production-auth-hardening` (SEC-01/02/03/04).
3. `03-audited-az-rule-pack-gate` (BUS-02 — production gating on `audited: true`).
4. `04-late-document-invalidation` (BUS-05).
5. `05-frontend-ci-coverage` (OTH-01).

## Non-goals

- Multi-state work of any kind (no `nj.yaml`/`ca.yaml`, no pack-schema fields for
  thresholds/PIP). Decided 2026-07-10: state #2 waits for a business signal.
- Time-limited demands (BUS-07) — excluded from the pilot box; the
  `missing_statutory_term` seam stays a no-op.
- Firm letterhead/template management (BUS-08) — separate work item; WI-1's gate is
  where template defects get *caught*, but template ingestion is not built here.
- Full exception-queue automation and confidence-ranked fact review — deferred to
  pilot signal (see WI-4 v0 scope).
- Any change to Brain-1/Brain-2 prompts, the compliance panel, or the money engine.

## Work items (implementation order)

### WI-1 — `package_review` gate: attorney approves the exact served bytes

**Why.** G3 approves rendered *content* before artifacts exist; `ARTIFACTS_BUILT`
currently lands directly in terminal `PACKAGE_READY`
(`backend/app/engine/orchestrator/machine.py:80,98`). Nobody approves the actual
DOCX/binder — letterhead, pagination, signature block, exhibit order. The final
instrument the firm signs and serves deserves a gate; every other legal output has one.

**Design (ADR-0009 first — one pager).**
- New `GateState.PACKAGE_REVIEW` between `package_assembly` and `package_ready`;
  `ARTIFACTS_BUILT` → `package_review` (gate, amber; NOT added to `auto_states`,
  `machine.py:103` — `package_assembly` stays auto).
- New events: `PACKAGE_APPROVED` (guards: `role_attorney`, `registry_version_match`)
  → `package_ready`; `PACKAGE_REJECTED` (guard: `role_attorney`, requires an
  enumerated reason) → `package_assembly` for a rebuild.
- **Rebuild mechanism (ADR-0009 decision; the naive loop does NOT work).** The
  builder is keyed on `(matter_id, draft_version, registry_version)` with a DB
  unique constraint (`backend/app/models/orm.py:820-826`,
  `uq_artifact_set_matter_versions`) and a reuse fast-path
  (`backend/app/package/build.py:86-94` → `reused=True`) — a presentation-only
  rejection changes neither draft nor registry version, so an unmodified re-run
  would return the SAME rejected set. Design: add `build_seq` (default 0) to
  `ArtifactSet` + widen the unique constraint to include it (migration);
  `PACKAGE_REJECTED` marks the current set rejected; the next build produces
  `build_seq + 1` and the reuse fast-path never returns a rejected set. (Bytes
  differ only after the underlying template/renderer defect is fixed — the gate's
  purpose is to catch exactly those out-of-band fixes; `build.py:116`'s
  "new version on drift" wording is scoped to content drift and does not cover
  this case.)
- Rejection reasons are presentation-only, closed set:
  `template_defect | pagination | signature_block | exhibit_order | letterhead_defect`.
  Content complaints are out of scope for this gate by construction — they route
  through the existing rework edges (G3 → drafting etc.); otherwise this becomes a
  second G3.
- Invalidation: `REGISTRY_BUMPED` at `package_review` cascades to `evidence_review`
  (consistent with the other post-freeze states); `package_ready` keeps refusing it
  (`machine.py:125` behavior unchanged). Lands AFTER audit plan 04
  (late-document invalidation), which extends the same matrix — rebase over it.
- Persistence: rejection audit row (matter, artifact_set version + build_seq,
  reason, actor, ts).
- **Invalidation module (missed by the review; required):**
  `backend/app/engine/orchestrator/invalidation.py` maps EVERY `GateState` to an
  `Effect` and `test_invalidation_covers_all_ten_states_exactly` hard-asserts exact
  coverage (`backend/tests/engine/test_invalidation.py:15-17`, `len == 10` → 11);
  a companion test requires each `Effect` to agree with the machine's
  `registry_bumped` edge. ADR-0009 decides whether `package_review` reuses an
  existing Effect or gains a new member (package staleness ≠ plan/draft staleness);
  both tests are extended in the same pass.
- SSE: existing `gate_ready` vocabulary fires for `package_review`; no new event kinds.
- FE: package inspection screen — artifact list with the exact bytes viewable
  (app-served blob route pattern from M6), approve / reject-with-reason actions.
- API/view-model surface: the gates route's `_view_model_for` dispatch gains a
  `PACKAGE_REVIEW` branch (`backend/app/api/routes/gates.py`), and
  `GATE_EVENT_BY_APPROVE` gains a sixth entry `PACKAGE_REVIEW → PACKAGE_APPROVED`
  (`backend/app/engine/orchestrator/service.py:220`).
- Docs: ADR-0009; update `docs/system_contract.md` +
  `docs/module_contracts/app.engine.orchestrator.md` +
  `docs/module_contracts/app.api.view_models.md` (its own change rule requires it
  for new routes/gate-action-map changes) + `CONTRACTS.md` same pass; update
  `systemflows/matter_lifecycle.md` and `systemflows/package_assembly.md` +
  regenerate their SVGs.

**Acceptance.**
- `package_ready` reachable only via `PACKAGE_APPROVED`; `PACKAGE_REJECTED` marks
  the set rejected, and the next `ARTIFACTS_BUILT` presents a NEW ArtifactSet row
  (`build_seq + 1`) — the reuse fast-path provably never re-presents a rejected set.
- Transition-table, guard, idempotency, and invalidation tests (incl. the
  exact-coverage and effect-agrees asserts) updated + green; `make verify` green;
  hub-check green.

**Size.** M (machine + enums + migration + FE screen + docs).

### WI-2 — Intake preflight: pilot eligibility box at matter creation

**Why.** Enforce the pilot restriction where effort starts, with scope-boundary
messaging (BUS-01 items 1–2), and surface urgent deadlines before any upload.

**Design.**
- `Matter` gains tri-state intake flags (`yes | no | unknown`):
  `public_entity_involved`, `plaintiff_is_minor`, `wrongful_death`,
  `coverage_dispute`. Migration + schema + create-API fields (all required in the
  create request; no silent defaults).
- Eligibility rule (v1): any flag ≠ `no` → typed 422 refusal in the
  `UnsupportedJurisdiction` style (`matters.py:55` pattern) with an
  attorney-readable reason per flag. Copy frames it as a v1 scope boundary
  ("outside v1 supported scope — handle in your existing workflow"), never a system
  error and never legal advice.
- `unknown` refuses (conservative): the attorney can create the matter after
  resolving the question; the refusal copy says exactly that.
- SOL visibility: matter creation already computes `sol_candidates` from
  `incident_date` + pack (`compute_deadline_candidates`,
  `backend/app/api/routes/matters.py:68`) and returns them in the create response;
  render them on the create screen result so an urgent SOL is visible before any
  document work. (No new computation — presentation only.)
- FE: eligibility section on `matter-create-form.tsx` (tri-state radios), refusal
  rendering per flag.
- Stored flags are shown read-only on the matter header (they are part of the
  file's audit story).
- Docs: `docs/module_contracts/app.api.view_models.md` (MatterView + create/refusal
  surface) and `docs/system_contract.md` (matter vocabulary) updated same pass —
  verified contract filenames; there is no separate "models"/"api" contract.

**Acceptance.** Out-of-box matters are refused with per-flag reasons; in-box matters
create exactly as today; flags persisted + displayed; API + FE tests (incl. refusal
copy shape); `make verify` + FE lint/test green.

**Size.** S–M.

### WI-3 — G2a "Ready to Demand?" attestation checklist (v0)

**Why.** G2a currently gates on risk-flag dispositions
(`high_severity_dispositioned_or_override`, `machine.py:65`) and shows the manifest
blocking preview — but ripeness facts no system should derive (MMI, liens, wage loss,
coverage) live only in the attorney's head. Make them explicit, attested, and audited.
Highest-leverage attorney input after G1.5.

**Design.**
- Fixed v1 attestation key set (wording to be confirmed by legal cofounder — open
  question 1): `treatment_complete_or_mmi`, `outstanding_records_resolved`,
  `liens_identified`, `wage_loss_addressed`, `coverage_confirmed`,
  `outstanding_care_considered`.
- Persistence: per-matter attestation rows (key, affirmed, actor, ts) — attested
  once, re-attestable; every change audited. New table → Alembic migration.
- Guard: new `attestations_complete` added to the `G2A_CONFIRMED` transition
  alongside the existing guards. Guard failure surfaces which keys are missing
  (inline error listing, consistent with existing guard-failure UX; no grayed-out
  button).
- Derived rows (read-only in the same checklist card, no new computation):
  adverse facts ← open risk-flag count; exhibit completeness ← manifest blocking
  preview. The card presents ONE "Ready to Demand?" surface; only the six
  attestation rows are inputs.
- No auto-population/detectors in v0 — that is post-pilot work driven by observed
  attorney behavior.
- Docs: `docs/module_contracts/app.engine.orchestrator.md` (new guard) +
  `docs/module_contracts/app.api.view_models.md` (checklist view-model); systemflows
  `evidence_review_g2a.md` + SVG updated.

**Acceptance.** G2a approve blocked until all six keys affirmed; attestations
persisted + audited + visible; existing G2a tests extended; `make verify` green.

**Size.** S–M.

### WI-4 — "Facts & Case Setup" workspace: G1+G1.5 visual merge + exceptions strip (v0)

**Why.** Reduce gate fatigue without touching legal approvals; surface ingest
exceptions where the attorney already is.

**Design.**
- FE-only. One workspace route presenting the G1 cards (deadline confirmations —
  `facts-review-card.tsx`) and the G1.5 strategy intake as sections of a single
  "Facts & Case Setup" screen. **Two distinct approval actions are preserved**
  (G1 approve; G1.5 submit) — no combined mega-approve; backend states, events, and
  guards untouched.
- Exceptions strip (v0): a summary band at the top aggregating existing signals —
  `zero_text` page count, open dedup-quarantine items, classification review-queue
  count — each linking to its existing surface. Reuses existing view-models where
  possible; at most one thin aggregation endpoint.
- No bulk-confirm and no confidence-ranked fact queues in v0 (G1 today confirms
  deadline candidates, which are few; the "review every fact" premise was stale).
  Revisit on pilot signal.
- `gate-stepper.tsx` shows the merged workspace as one step with two sub-approvals.
- Docs: none if the strip reuses existing view-models; IF the thin aggregation
  endpoint is added, `docs/module_contracts/app.api.view_models.md` +
  `docs/system_contract.md` update in the same PR (the contract's change rule
  covers any new REST route).

**Acceptance.** Both approvals function unchanged (API calls identical); exceptions
strip counts match the underlying surfaces; FE tests for the merged route; FE
lint/test/build green (lint clean — this repo carries no lint-debt baseline).

**Size.** S.

## Sequencing

Audit queue (01–05, above) → WI-1 (ADR-0009, then implementation) → WI-2 → WI-3 →
WI-4. Linear — one founder, WIP=1. WI-2/WI-3 are independent of WI-1 and can be
pulled earlier if WI-1's ADR discussion stalls.

## Risks

- **WI-1 touches the transition table** — the invalidation matrix and idempotency
  tests are the safety net; extend them before changing `TRANSITIONS`.
- **WI-3's new guard breaks existing fixtures** that drive matters through G2a —
  seed helpers must affirm attestations; audit the test-fixture path in the same PR.
- **WI-2 migration on existing rows** — dev-only data; backfill flags as `unknown`
  (which would refuse *new* creation but must not retroactively block existing
  matters' gate progress — eligibility is a creation-time check only).
- **Copy risk (WI-2/WI-3)** — refusal and attestation wording is attorney-facing
  legal-adjacent text; legal cofounder reviews copy before pilot.

## Open questions

1. Attestation key set + exact wording (legal cofounder) — v0 ships with the six
   keys above marked `verify — counsel` in code comments until confirmed.
2. Should `PACKAGE_APPROVED` also require the active pack `audited: true` (ties to
   audit plan 03), or is that gate global at matter creation? Proposal: global at
   creation once plan 03 lands; not duplicated per-gate.
3. Does the pilot firm want paralegal-preparable attestations (paralegal stages,
   attorney affirms)? v0 = attorney-only, consistent with `role_attorney` guards.
4. **WI-1 × audit plan 04 interaction (design call for ADR-0009):** audit-04 has
   ALREADY decided this in writing — it adds `GateEvent.NEW_CYCLE_STARTED` /
   `GateAction.START_CYCLE` with a machine transition `package_ready →
   evidence_review` (attorney-only, only when the registry is newer than the
   packaged draft) and makes `terminal_states` EMPTY
   (`docs/audit/plans/04-late-document-invalidation.md:193-199`). Since audit-04
   ships before WI-1, ADR-0009's question is narrower than first framed: how
   `package_review` composes with a non-terminal `package_ready` — specifically
   (i) whether this plan's "`package_ready` keeps refusing `REGISTRY_BUMPED`
   (`machine.py:125` unchanged)" still holds after audit-04 rewrites that edge and
   its self-loop test, and (ii) confirming both `START_CYCLE` and
   `package_review`'s cascade target the same `evidence_review` re-entry so the
   invalidation `Effect` story stays coherent. WI-1 rebases on audit-04's landed
   design; the two-candidate framing in earlier drafts is superseded.
