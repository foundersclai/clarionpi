# PI Agent — Implementation Readiness Memo

- **Status:** CANONICAL — this memo reconciles all supersessions and records go/no-go state;
  update it whenever a blocker closes or a decision lands. · **Date:** 2026-07-04
- Origin: external Codex readiness review (2026-07-04). Verdict adopted: **ready for M0
  scaffolding, module-contract writing, and S1/S2/S4 spikes; NOT ready for live-client/ABS
  execution** until the P0 blockers below close.

## 1. The one-paragraph state

The design suite (00–09, components/, system_flows/) is internally consistent and deep
enough to start building against synthetic data today. Everything that blocks going further
is **real-world, not design**: ethics counsel hasn't papered the structure, the AZ managing
attorney doesn't exist yet, the fee/CPA case model is unvalidated, and no BAA is signed.
Those block the *captive-firm* and *live-PHI* release stages — they do not block code.

## 2. P0 blocker dashboard

| # | Blocker | Owner | Blocks stage | Exit evidence |
|---|---|---|---|---|
| B1 | Gate M-1: ethics counsel engaged; structure memo; equity split (NewCo / Bao / attorney, holding-co question); FMV license/MSA terms; control-independence rules; malpractice + corporate-separateness plan; TM↔NewCo IP assignment ([07 §7](./07_captive_firm_model.md)) | Legal cofounder + founders | **R2** | Counsel-signed structure memo + executed papers |
| B2 | AZ PI managing attorney hired (equity-heavy comp per [09 §5](./09_bootstrap_abs_path.md); dual-hats Compliance Lawyer) | Legal cofounder (search), founders (close) | **R2** | Signed offer; named in ABS application |
| B3 | Case-model validation: Phoenix fee size, CPA, referral economics, settlement cycle, attorney comp appetite ([08 §3](./08_seed_plan_and_budget.md) assumptions) | Founders + Bao | **Funding decision + R2** | Operator-interview write-up; assumptions revised in 08/09 |
| B4 | BAA/vendor stack: LLM path (Anthropic ZDR+BAA vs Bedrock), Textract/AWS, hosting account split, pdf-viewer perf check ([03 §8](./03_tech_stack.md), [11 §4](./11_spike_briefs.md)) | Engine dev; legal cofounder countersigns | **R2 (live PHI)** | Signed BAA inventory checked in |

None of B1–B4 blocks R0/R1. Development proceeds on synthetic/de-identified fixtures only
until B4 closes ([11 §1](./11_spike_briefs.md) fixture rules).

## 3. Release matrix

| Stage | Scope | Entry criteria | Exit criteria |
|---|---|---|---|
| **R0 — scaffold + spikes** | M0 repo, contracts bundle, S1/S2 on synthetic corpus | now | `make verify` green; §5 bundle merged; S1/S2 pass thresholds ([11](./11_spike_briefs.md)) or rescope executed |
| **R1 — synthetic MVP** | End-to-end demand on de-identified fixtures (M1–M7) | R0 exit | G1→package on 3 fixture matters; Tier-1 suite green; zero unanchored facts; provenance round-trip works |
| **R2 — captive-firm pilot** | First real matters, founder-supervised | B1+B2+B3+B4 closed; ABS licensed; [12](./12_abs_ops_runbook.md) adopted by counsel | First real demand shipped w/ zero unanchored facts; runbook cadences running |
| **R3 — live-PHI production** | Full caseload, steady operations | R2 across ≥5 matters; HIPAA checklist audit; incident process exercised | Sustained ops; audit reviews clean; unit economics measured vs [08 §3](./08_seed_plan_and_budget.md) |
| **R4 — v1.x** | Comparables, assistant, HybridEngine fallback, lost wages ([05 M8](./05_implementation_plan.md)) | R3 stable | Per-feature acceptance in [02](./02_feature_list.md) |

<!-- workshop-mvp-r1-overlay:start -->
### Workshop MVP R1 overlay

The Workshop MVP is an R1 demonstration overlay only. Its evidence is owned-synthetic and cannot
close the legal, PHI, ethics, or live-pilot gates for R2.
<!-- workshop-mvp-r1-overlay:end -->

## 4. Decisions ledger (2026-07-04)

Gate-semantics and build-rule decisions closed by this review; each is recorded in place in
the owning doc.

| # | Decision | Where recorded |
|---|---|---|
| D1 | Low-confidence classification never blocks Phase 0 — proceed flagged; facts from flagged docs are `unverified`; reclassify re-runs that doc only. Non-AZ matters refused with a typed reason (no fallback) | [flow_01 §6](./system_flows/flow_01_intake_to_facts_review.md) |
| D2 | `need_more_records` leaves the flag OPEN; G2a confirm over an open high-severity flag = `requires_override` (reason, audited). MMI date = explicit attorney field at G1.5 | [risk_flag_engine §8](./components/risk_flag_engine.md) |
| D3 | `[[CITE_n]]` v1 origins: jurisdiction-rules statutes + attorney-supplied only — **no LLM-proposed authority in v1**. Strict single drafting retry. Strategy memo is an attorney-visible artifact | [brain2_drafting §8](./components/brain2_drafting.md) |
| D4 | Missing statutory term = mechanical insert-at-anchor from the rule pack (regen only when the pack flags prose-integration). Attorney-overridden semantic findings enter the E4 report as audited judgment calls. Judge = Sonnet until a fixture A/B says otherwise. G3 UI renders mechanical/semantic × blocking/advisory | [compliance_engine §8](./components/compliance_engine.md) |
| D5 | Tenancy build rule: every table carries `firm_id` + scoped-session enforcement from day one; single-tenant is an operational posture only (one firm row, no signup UI) — no schema/query shortcuts | [07 §5](./07_captive_firm_model.md), [platform_core](./components/platform_core.md) |

### Supersession ledger (cumulative)

| Item | Old | Now | Decided |
|---|---|---|---|
| Strategic fit | (a) vs (b) open | Separate NewCo, captive-firm GTM | [00 §8](./00_vision_and_scope.md), 2026-07-03 |
| Funding | VC assumed | Two documented tracks, decision open until M-1 spend | [08](./08_seed_plan_and_budget.md)/[09 §7](./09_bootstrap_abs_path.md), 2026-07-04 |
| Pilot firm (S3) | External design partner | Captive firm IS the pilot | [07 §5](./07_captive_firm_model.md) |
| Launch states | 3–5, pilot-driven | Arizona only | [07 §4–5](./07_captive_firm_model.md) |
| E4 provenance report | v1.x | MVP, built in M6 | [02 E4](./02_feature_list.md), [05 M6](./05_implementation_plan.md) |
| North star | Product metric | Firm unit economics (product bar still holds) | [00 §10](./00_vision_and_scope.md), [07 §5](./07_captive_firm_model.md) |
| StrategyPlan / DraftSection ownership | Unowned | orchestrator_gates / brain2_drafting | [components/](./components/README.md), 2026-07-04 |

## 5. M0 module-contract bundle (the "schemas are design-level" close-out)

M0 exits only when this bundle is merged in the new repo (now in
[05 M0](./05_implementation_plan.md) exit criteria):

1. **Schemas as code:** Pydantic v2 models for every 04 §2 entity; all enums (gate states,
   doc types, flag kinds, finding kinds/buckets, disposition values) as typed enums.
2. **Alembic baseline** migration matching the models; `firm_id` on every firm-scoped table (D5).
3. **Gate-transition guard table:** the orchestrator's `(state, event) → state + guards`
   enumeration as data, with the invalidation matrix from
   [flow_04](./system_flows/flow_04_late_records_rework.md).
4. **Idempotency-key scheme** for gate submissions and run enqueues.
5. **Audit event schema** (append-only; gate actions, overrides, PHI page access, artifact builds).
6. **API contract tests** for every 04 §3 endpoint + SSE event shape (04 §4), asserting the
   wire discipline (no tokens, no reasoning events).
7. **The 12 module-contract docs** (04 §5 names) seeded from their
   [components/](./components/README.md) designs — vocabulary, imports allowed, events,
   invariants w/ Tier-1 test refs.

## 6. Codex findings → disposition

| Finding | Disposition |
|---|---|
| P0 Gate M-1 open | Real-world; tracked as B1 with owner + stage gate (§2) |
| P0 attorney + case model | Real-world; B2/B3 (§2) |
| P0 BAA/vendor | Real-world; B4 (§2) + [11 §4](./11_spike_briefs.md) checklist |
| P1 stale pilot/launch-state questions in 05 §5 | **Fixed** — [05 §5](./05_implementation_plan.md) rewritten |
| P1 provenance report still in M8 | **Fixed** — removed from M8, noted as MVP/M6 |
| P1 schemas design-level | **Addressed** — §5 bundle now an M0 exit criterion |
| P1 gate-semantics open decisions | **Decided** — D1–D4 (§4) |
| P1 spike briefs lack protocols | **Written** — [11_spike_briefs.md](./11_spike_briefs.md) |
| P2 tenancy build rule | **Decided** — D5 (§4) |
| Ask: readiness memo | This document |
| Ask: ABS/legal ops runbook | **Written** — [12_abs_ops_runbook.md](./12_abs_ops_runbook.md) (scaffold; counsel finalizes) |
| Ask: release matrix | §3 |
