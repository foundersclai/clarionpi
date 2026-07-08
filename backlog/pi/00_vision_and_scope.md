# PI Agent — Vision & Scope

- **Status:** DRAFT for founder review
- **Date:** 2026-07-03
- **Working name:** PI Agent (target: new repository `pi-agent`, not this repo — see [03_tech_stack.md](./03_tech_stack.md) §7)
- **Reading order:** this doc → [01_high_level_design.md](./01_high_level_design.md) → [02_feature_list.md](./02_feature_list.md) → [03_tech_stack.md](./03_tech_stack.md) → [04_data_model_and_contracts.md](./04_data_model_and_contracts.md) → [05_implementation_plan.md](./05_implementation_plan.md)

---

## 1. Problem

At plaintiff-side Personal Injury (PI) firms, the **pre-litigation demand package** is the
revenue bottleneck:

- A demand takes **5–20 paralegal-hours**: reading hundreds/thousands of pages of medical
  records, building a treatment chronology, itemizing bills, drafting the letter, collating
  exhibits.
- Firms run **weeks-to-months of backlog** between "client done treating" and "demand out
  the door" — every week of delay is delayed settlement and delayed contingency fee.
- Quality is **high-variance**: missed billing lines (under-demanded specials), missed
  treatment gaps (ambush at negotiation), inconsistent letter quality across paralegals.
- Deadline exposure is **malpractice-grade**: statutes of limitations and (worse)
  short government notice-of-claim deadlines are tracked in spreadsheets.

## 2. Product thesis — "the auditable demand"

An agentic pipeline that turns a raw case file (medical records, bills, police report,
wage docs, photos) into an attorney-approved demand package, where:

1. **Every factual assertion is page-anchored** — click any fact in the letter and see the
   source document page it came from.
2. **Every dollar figure is computed deterministically** — the LLM never does arithmetic;
   it references amount tokens resolved from a specials ledger.
3. **Attorneys and paralegals stay in control via gates** — the system prepares, humans
   approve; every approval and override is audited.

This is the TMEPAgent chassis (Two-Brain pipeline, HITL gate machine, tokenize-or-omit
anti-fabrication, per-fact provenance) re-targeted at a bigger market. The differentiation
logic is identical to the TM product's positioning vs TMTKO: incumbents (EvenUp et al.)
have publicly documented fabrication incidents in generated demands; **provenance + HITL +
malpractice-protection posture** is the counter-position.

## 3. Target user & wedge

- **User:** plaintiff PI firms, ~2–25 attorneys, paralegal-heavy staffing (3–10 paralegals
  per attorney is common). Buyer = managing partner; daily users = paralegals (prep) and
  attorneys (strategy + sign-off).
- **Wedge:** the pre-litigation demand for **motor-vehicle accident (MVA) cases** — the
  highest-volume, most standardized PI case type. Land on demands; expand to negotiation
  support and litigation artifacts (v2).
- **MVP scope guard:** single claim type (MVA, adult plaintiff, no wrongful death),
  3–5 launch states chosen with the pilot firm, pre-litigation demand only.

## 4. The deliverable

One **demand package** per matter:

| Artifact | Form | Notes |
|---|---|---|
| Demand letter | `.docx` (editable) | Attorneys edit in Word; never lock them into a viewer |
| Medical chronology | Appendix + `.xlsx` export | Encounter-by-encounter, page-cited |
| Specials ledger | Table in letter + appendix | Billed / adjusted / paid per jurisdiction rule |
| Exhibit binder | Collated PDF | Bookmarks, exhibit index, Bates-style stamping, page-level include/exclude |
| Provenance map | In-app | Fact → (document, page) click-through; ships with the matter record, not to the carrier |

## 5. What we carry over from TMEPAgent (and what it becomes)

| TMEPAgent concept | PI Agent analog | Disposition |
|---|---|---|
| Office Action (single adversarial input doc) | Case file corpus (no single "prompt" doc; the input is 100–2,000 pages) | **Adapted** — Phase 0 becomes corpus-scale ingest |
| OA Response deliverable | Demand package | **Carried** |
| `ProsecutionCorpus` / `CaseDocument` + provenance sidecar | `CaseCorpus` / `CaseDocument` / `DocumentPage` with page-level provenance | **Carried**, page-granular |
| Two-Brain split (Brain-1 research, Brain-2 strategist/drafter) | Brain-1 = extraction/chronology/risk/comparables; Brain-2 = strategy memo + demand letter | **Carried** |
| Gate machine G1 → G3 (7 states) | G1 facts+deadlines, G1.5 strategy intake, G2a evidence review, G2.5 demand plan, G3 compliance | **Carried** + role-aware (paralegal prep vs attorney sign-off) |
| `[[CITE_ID_X]]` tokens block fabrication | Token namespaces: `[[FACT_*]]` `[[AMT_*]]` `[[CITE_*]]` `[[EX_*]]` | **Carried**, extended to facts and amounts |
| `citation_legend` isolated on session state | `fact_registry` on matter state (not on the dossier/artifacts) | **Carried** (blast-radius isolation) |
| Three-lane partition (legend / evidence_appendix / intelligence layer) | fact_registry / exhibit_appendix / intelligence_layer | **Carried** |
| Rules-first YAML + LLM fallback (`app/engine/routing` HybridEngine) | Jurisdiction rules: SOL, notice deadlines, comparative fault, damages caps, billed-vs-paid, time-limited-demand statutes | **Carried** — lawyer-audited YAML, engineer-owned Python |
| Evidence appendix + exhibits pipeline | Exhibit binder with Bates + bookmarks | **Carried** |
| Examiner intelligence (re-rank by examiner behavior) | Carrier/adjuster intelligence (re-rank arguments by insurer behavior) | **Deferred to v2** |
| `assistant_lite` (read-only in-matter Q&A) | Same, answers carry page citations | **Carried (v1.x)** |
| Eval tiers 1 / 1.5 / 2; golden fixtures from live cases | Same; fixtures are PHI-scrubbed pilot-firm matters | **Carried** |
| Per-matter LLM cost metering + caps | Same — **wired and ON from day 1** (TM lesson: built-but-off undercounts) | **Carried, hardened** |
| TTAB/CourtListener corpora | Verdict & settlement comparables corpus + firm's own closed cases | **Adapted** |
| Attribution sidebar (citation → source) | Provenance viewer (fact → page) | **Carried, promoted to core moat** |
| Compliance gate: mechanical span-patch vs semantic regen | Same bucketing at G3 | **Carried** |
| SSE streaming, view-model-only AI overlays, no reasoning events on wire | Same wire discipline | **Carried** |

**Deliberately NOT carried:** trademark doctrine modules (DuPont, §2(e)(1) packets),
TMEP RAG, per-cited-mark consent, foundational-citation floors, TSDR pull, coverage-gap
expansion. See [01_high_level_design.md](./01_high_level_design.md) §11.

## 6. What is genuinely new (no TM analog)

1. **OCR at scale** — 100–2,000 page case files, faxed/scanned/handwritten records. This is
   the make-or-break engineering risk (Spike S1 in the implementation plan).
2. **Medical entity extraction** — encounters, ICD-10/CPT codes, providers, work
   restrictions; encounter dedup/merge (the same visit arrives in 3 different record pulls).
3. **Deterministic money engine** — specials ledger, billed/adjusted/paid, lost wages,
   demand math. Property-tested pure code.
4. **HIPAA envelope** — PHI everywhere; BAA inventory for every egress (LLM, OCR, cloud);
   de-identified eval fixtures; third-party-patient redaction. Trademark data was public.
5. **Role-aware gates** — paralegals prepare (chronology edits, evidence picks), attorneys
   sign (strategy, plan, compliance). TM product was attorney-only.
6. **Adverse-fact risk flags** — treatment gaps, pre-existing conditions, degenerative
   findings, prior claims: *surface always to the attorney, volunteer never in the letter*
   (the PI translation of "no volunteering adverse precedent").

## 7. Competition & positioning

- **EvenUp** (category leader, demand packages), **Supio**, **Parrot**, **DigitalOwl/
  Wisedocs** (record summarization only). The category is validated; nobody owns
  **auditable provenance + HITL gates** as the product spine.
- Positioning sentence: *"Demands your malpractice carrier would approve of — every fact
  anchored to a page, every number computed, every judgment call signed by your attorney."*
- Data moat seed: the pilot firm's **own closed-case history** becomes its private
  comparables corpus (settlement outcomes by injury/venue/carrier) — per-firm value that
  compounds and doesn't require licensing verdict data on day 1 (same revenue-gating lesson
  as the Westlaw/add-citation decision in the TM product).
- Pricing instinct (not designed here): hybrid subscription + per-demand metering, mirroring
  the TM hybrid sub+overage model. Per-matter cost metering makes this billable from day 1.

## 8. Strategic fit — honest note

The standing exit thesis (Fast & Modest: 24-month exit to an Alt Legal-shaped acquirer,
no expansion, team ≤ 5) **rules out** launching a second vertical. This design is therefore
one of:

- **(a)** a **separate bet** with its own acquirer pool (EvenUp competitors; Filevine /
  Litify / Clio ecosystem buyers), consciously funded/staffed apart from the TM exit asset, or
- **(b)** a **chassis-proof exercise** — evidence that the TM architecture generalizes,
  which strengthens the "vertical legal agents platform" story for *either* asset.

**Direction adopted 2026-07-03: (a), sharpened** — a separate company (Delaware C-corp)
pursuing a **captive-firm GTM**: found an Arizona ABS PI firm with Bao + an AZ managing
attorney, run it on our software, participate via ABS equity. **Funding is a two-track
decision (opened 2026-07-04), both documented:** venture-backed ($2.5M seed,
[08_seed_plan_and_budget.md](./08_seed_plan_and_budget.md)) vs bootstrapped (~$300–450K
founder float, [09_bootstrap_abs_path.md](./09_bootstrap_abs_path.md) — the variant
consistent with the standing Fast & Modest philosophy). The TM asset stays on its own
path with clean IP separation either way. See also
[06_competitive_landscape.md](./06_competitive_landscape.md) and
[07_captive_firm_model.md](./07_captive_firm_model.md).

## 9. Out of scope (v1)

- Defense-side PI, medical malpractice (expert/certificate-of-merit driven), mass tort.
- Case management replacement — integrate with Filevine/Litify/Clio later, don't compete.
- Client-facing portal, treatment monitoring, lien *negotiation* (tracking is v2).
- Litigation drafting (complaint, discovery) — v2 escalation path.

## 10. North-star metric

> An attorney-accepted demand with **≤ 30 minutes of attorney touch time**,
> **zero unanchored facts**, **zero arithmetic errors**, at **≤ $25 LLM+OCR COGS**.

Secondary: paralegal prep time ≤ 2 hours (from 5–20); demand turnaround ≤ 5 business days
from record-complete.

*Under the adopted captive-firm model the **venture** north star becomes firm unit
economics — contribution per case including acquisition cost ([07 §5](./07_captive_firm_model.md),
[08 §3](./08_seed_plan_and_budget.md)). The product bar above still holds; it is what
makes the firm economics work.*
