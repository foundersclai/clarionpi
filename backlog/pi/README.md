# PI Agent — Design Suite

Design plans for a Personal Injury (plaintiff-side) analog of the TM system: an agentic
pipeline that turns a raw case file (medical records, bills, police report) into an
attorney-approved **demand package** with page-level provenance for every fact, a
deterministic money engine, and HITL gates throughout.

- **Status:** DRAFT — for founder review; no code exists yet
- **Created:** 2026-07-03
- **Target:** new repository `pi-agent` (these plans live here because planning lives in
  `backlog/`; the code will not)

## Thesis in one paragraph

The pre-litigation demand is PI's Office Action response: a document-heavy, semi-structured,
high-stakes deliverable currently costing 5–20 paralegal-hours. The TMEPAgent chassis —
Two-Brain pipeline, G1→G3 gate machine, tokenize-or-omit anti-fabrication, per-fact
provenance, lawyer-audited rules YAML, per-matter cost metering — transfers almost intact.
What's new: OCR-scale ingest, medical entity extraction, a property-tested money engine,
the HIPAA envelope, and role-aware gates (paralegal prep, attorney sign-off). Positioning:
**the auditable demand** — every fact page-anchored, every number computed, every judgment
signed.

## Reading order

| Doc | Contents |
|---|---|
| [00_vision_and_scope.md](./00_vision_and_scope.md) | Problem, thesis, wedge, TM→PI concept mapping, competition, strategic-fit note, north star |
| [01_high_level_design.md](./01_high_level_design.md) | 14 design invariants, system context, Two-Brain pipeline, gate machine, provenance architecture, rules layer, risk flags (diagrams) |
| [02_feature_list.md](./02_feature_list.md) | 59 features grouped A–H, tiered MVP / v1.x / v2, acceptance one-liners |
| [03_tech_stack.md](./03_tech_stack.md) | Stack decision table, TM port list, HIPAA envelope, deployment diagram, cost model, repo strategy |
| [04_data_model_and_contracts.md](./04_data_model_and_contracts.md) | ER diagram, core schemas + invariants, API + SSE vocabulary, module map + contract list |
| [05_implementation_plan.md](./05_implementation_plan.md) | Spikes S1–S4, milestones M0–M8 with exit criteria, gantt, team split, risk register, open questions |
| [06_competitive_landscape.md](./06_competitive_landscape.md) | EvenUp deep-dive (verified 2026-07), gap analysis, why we don't fight for SaaS seats |
| [07_captive_firm_model.md](./07_captive_firm_model.md) | **Adopted GTM**: AZ ABS captive firm — routes A/B/C, entity structure, AZ-only v1, risks, gates |
| [08_seed_plan_and_budget.md](./08_seed_plan_and_budget.md) | **Funding track A — VC**: 24-mo budget (2 founders + India eng), case model, $2.5M seed ask, VC narrative, Series A bar, kill criteria |
| [09_bootstrap_abs_path.md](./09_bootstrap_abs_path.md) | **Funding track B — bootstrap**: referral-first channels, ~$300–450K founder float, 5-yr ramp to $3–5M/yr, bootstrap-vs-VC table, switch criteria |
| [10_implementation_readiness.md](./10_implementation_readiness.md) | **CANONICAL readiness memo**: P0 blocker dashboard (B1–B4 w/ owners), release matrix R0–R4, decisions ledger D1–D5, supersession ledger, M0 contract-bundle checklist |
| [11_spike_briefs.md](./11_spike_briefs.md) | Executable S1/S2/S4 protocols: frozen fixture corpus (no live PHI), gold-label ownership, scoring definitions, rescope triggers |
| [12_abs_ops_runbook.md](./12_abs_ops_runbook.md) | ABS operations runbook **scaffold** (counsel finalizes): independence protocol, intake/solicitation, PHI SOP, separateness hygiene, audit cadences |

All eighteen architecture diagrams are standalone SVGs in [`diagrams/`](./diagrams/) —
embedded inline in each doc, with the editable Mermaid source kept in a collapsible
block beneath each image (edit the Mermaid → re-export the SVG).

## Level 2 — component design & system flows

| Folder | Contents |
|---|---|
| [components/](./components/README.md) | 14 component designs (responsibility, boundary tables, key types, invariants enforced, failure modes, test strategy) + the component map |
| [system_flows/](./system_flows/README.md) | 5 end-to-end flows with detailed sequence diagrams: intake→G1, strategy→G2a confirm, demand→package, late-records rework/invalidation, provenance round-trip |

Components are the static design (one doc per module in [04 §5](./04_data_model_and_contracts.md));
flows are the dynamic design (exact endpoints, SSE events, and state transitions per step).

## Decisions & open items

**Decided 2026-07-03** ([00 §8](./00_vision_and_scope.md)): separate NewCo (Delaware
C-corp) pursuing the **captive-firm GTM** — Arizona ABS PI firm co-owned with Bao + an AZ
managing attorney, software licensed at FMV, economics via ABS equity
([07](./07_captive_firm_model.md)). This supersedes the external-pilot plan (S3) and
collapses launch states to Arizona.

**Funding path — open (2026-07-04), both tracks documented:** VC seed $2.5M
([08](./08_seed_plan_and_budget.md)) vs bootstrap ~$300–450K founder float
([09](./09_bootstrap_abs_path.md), the Fast-&-Modest-consistent variant). Same firm,
same software, same gates — the tracks differ only in throttle and ownership; the switch
stays open until Gate M-1 money is spent ([09 §7](./09_bootstrap_abs_path.md)).

**Readiness (external Codex review, 2026-07-04):** ready for **M0 scaffolding +
module contracts + S1/S2/S4 spikes** on synthetic fixtures; live-client/ABS execution is
gated by four real-world blockers — B1 ethics counsel/structure, B2 AZ managing attorney,
B3 case-model validation, B4 BAA stack — tracked with owners and release-stage gates in
[10_implementation_readiness.md](./10_implementation_readiness.md) (the canonical
supersession + decisions ledger). Spike protocols: [11](./11_spike_briefs.md). Ops
runbook scaffold for counsel: [12](./12_abs_ops_runbook.md).
