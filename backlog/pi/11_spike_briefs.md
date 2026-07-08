# PI Agent — Spike Briefs (S1 / S2 / S4)

- **Status:** DRAFT · **Date:** 2026-07-04
- **Purpose:** executable protocols for the de-risking spikes in [05 §0](./05_implementation_plan.md) —
  fixture corpora, gold-label ownership, scoring rules, decision procedure, and rescope
  triggers bound to the kill criteria there. S3 (pilot firm) is superseded by the captive
  firm ([07 §5](./07_captive_firm_model.md)) and needs no spike.
- **Hard constraint:** **no live PHI before the BAA stack is signed (S4).** All S1/S2
  fixtures are synthetic or public de-identified material — never live client records
  ([03 §3](./03_tech_stack.md) rules 1, 5).

## 1. Shared fixture corpus (FC set)

One frozen corpus feeds S1 and S2, so OCR and extraction are scored on the same pages. It is
built **entirely without live PHI**: synthetic documents (realistic templates + injected
noise), public de-identified teaching files, and founder-created scans of synthetic documents
run through real fax/scan hardware to reproduce authentic degradation. Freeze as **FC-v1**
before any vendor run; additions create **FC-v2** — no silent drift.

| Set | Content | Size | Stresses | Pre-BAA sourcing |
|---|---|---|---|---|
| **FC-1** | Clean EMR export — typed, text-layer present | ~300 pp | Text-layer fast path; the easy baseline | Synthetic EMR templates |
| **FC-2** | Degraded mixed — fax artifacts, skew, stamps, low-DPI rescans, margin handwriting | ~400 pp | OCR fallback under real noise (the make-or-break set) | Synthetic docs printed → faxed/rescanned on real hardware |
| **FC-3** | Handwritten + checkbox forms — provider intake, PT flowsheets | ~150 pp | Handwriting + checkbox capture | Founder-filled synthetic forms, scanned |
| **FC-B** | Bills pack — CMS-1500 / UB-04-style tables, EOBs, itemized statements | ~80 pp | Table fidelity + dollar-exactness for the money engine | Synthetic bill templates with realistic line items |

**Provenance:** every page records how it was generated/sourced; **no page originates from a
live client matter.** Real pilot-matter fixtures are a separate, later track (safe-harbor
de-identified — [05 M7](./05_implementation_plan.md)) and do not enter FC-v1.

## 2. S1 — OCR vendor bake-off

- **Question ([05 §0](./05_implementation_plan.md)):** ≥98% usable page text + faithful bill
  tables at ≤$8/case?
- **Contestants:** AWS Textract, Google Document AI, Azure Document Intelligence; **Tesseract
  as the floor/reference** (the free baseline every paid vendor must beat).
- **Owner:** data cofounder. **Effort:** 1–2 weeks. **Artifacts:** one scorer script + a
  results table, checked into the `pi-agent` repo.

**Protocol.** Same frozen FC-v1, one scorer script, all vendors run within one week.
**Results are blind-scored before cost is revealed** — no vendor identity attached to a
transcript during grading — so an expensive vendor can't buy a favorable read.

**Gold-label protocol (data cofounder owns).**
- **Full-text transcripts:** 10% stratified page sample per FC set (stratified across noise
  levels, not first-N pages).
- **Bill tables:** **100% of FC-B**, transcribed cell-level (this is the money-engine
  ground truth).
- Ambiguous cells/tokens adjudicated by the **legal cofounder**; the ruling is recorded.
- **Every gold label records its provenance** (who produced it, source page) — gold is
  auditable, not asserted.

**Scoring definitions (each made precise).**
- **Page-text coverage** = token-level recall vs the gold transcript after Unicode +
  whitespace normalization. **Thresholds: ≥98% on FC-1, ≥95% on FC-2.**
- **Table fidelity** = cell-level **F1 ≥97%**, with **dollar amounts exact-match (no
  tolerance)** — a wrong cent fails the cell.
- **Cost/latency** = per-page latency and **$/1K pages taken from vendor billing** (actuals,
  not list price).
- **Confidence calibration** = do vendor confidence scores actually predict errors? Needed to
  design the review queue ([05 M1](./05_implementation_plan.md), low-confidence page queue) —
  a vendor whose confidence is uninformative forces more manual review.

**Decision rule.** Winner = **passes both coverage thresholds at the lowest $/1K pages.**
Ties break on confidence calibration (better error-prediction wins).

**Rescope triggers (bind to [05 §0](./05_implementation_plan.md) kill criteria).**
- No vendor reaches **≥95% token recall on FC-2** → MVP intake **narrows to text-layer +
  clean scans**; revisit the degraded-fax path quarterly (matches the 05 OCR kill criterion).
- **FC-B F1 <90% for every vendor** → **billing extraction goes human-in-loop** (review
  queue) at MVP rather than shipping unreliable auto-extracted specials.

## 3. S2 — extraction fidelity

- **Question ([05 §0](./05_implementation_plan.md)):** Sonnet structured outputs ≥95%
  encounter recall with correct anchors?
- **Owner:** engine dev runs the extractor; **data cofounder scores** against gold.
- **Input:** post-OCR text from the **S1 winner** (S2 depends on S1 being decided).

**Gold set.** 50-page windows drawn from FC-1 and FC-2, fully gold-labeled:
- **Encounters:** DOS, provider, facility, type, dx, procedures.
- **Billing lines:** amounts to the penny.
- **Anchor:** the exact page(s) each fact appears on.

Bounded 50-page windows are what make anchor-accuracy checkable at the page level.

**Metrics.**
- **Encounter recall ≥95%**, **precision ≥90%** — recall dominates: over-extraction is
  reviewable at G2a, under-extraction is invisible (a missed encounter never surfaces).
- **Field accuracy ≥98% on DOS + provider** (the chronology's spine).
- **Anchor accuracy ≥98%** — a fact's cited page **must contain the fact; exact page, no
  ±1 tolerance.** This is the anti-fabrication invariant made measurable.
- **Billing totals reconcile exactly** to the gold ledger (cents-exact, feeds the money
  engine).
- **Merge/dedup measured separately:** duplicate-encounter rate on a **doubled-document
  fixture** (the same records ingested twice) — merge precision is its own number, not folded
  into recall.

**Procedure.** **≤3 prompt-iteration rounds.** Each round logs its prompt version + scores
(so improvement is attributable, not vibes).

**Rescope trigger (from [05 §0](./05_implementation_plan.md)).** **<90% recall after round 3**
→ add a **page-classification pre-pass** and re-scope **M2 (+1 week)** — matches the 05 S2
kill criterion exactly.

## 4. S4 — BAA / vendor path

Not a benchmark — a **paperwork-and-sequencing checklist.** Every agreement below is a legal
instrument; terms are **(verify — counsel)**. Start all paperwork now (longest lead time in
the plan); **nothing here blocks synthetic-only development.**

| Vendor / surface | Needed agreement | Owner | Blocks which stage |
|---|---|---|---|
| LLM provider — Anthropic (enterprise ZDR + BAA) **or** AWS Bedrock (BAA under AWS umbrella) | BAA + ZDR terms; **decide by M1 exit** ([03 §8](./03_tech_stack.md)) | Eng + counsel | R2 (first live matter) |
| AWS — S3 / Textract / hosting | Cloud BAA + **separated prod account** ([03 §8](./03_tech_stack.md)) | Eng + counsel | R2 |
| Error tracking — self-hosted GlitchTip | **No BAA needed** — confirm no PHI leaves the box | Eng | None (config gate, not paperwork) |
| E-mail / notifications | Only if PHI touches it — **prefer none** (keep PHI out of e-mail) | Eng + counsel | R2 *iff* used |
| EOR for India engineers | **No PHI access by design** — fixtures only; note the contractual boundary | Founders + EOR | None (boundary, not egress) |

**Sequencing.** Paperwork starts at M0. **R0/R1 run on synthetic fixtures — the unsigned
stack blocks neither.** The **signed stack gates R2 (first live matter).**

**Artifact.** A **BAA inventory doc** lives in the `pi-agent` repo and is **updated on any new
egress** — the [03 §3](./03_tech_stack.md) rule 1 discipline (nothing touches PHI unless it's
on the list), enforced as a living document, not a one-time sign-off.

## 5. Cross-spike governance

- Results land in a **`RESULTS.md`** next to the scorer script.
- **A spike is DONE only when its decision is written down with the data attached** — a
  number without a recorded decision is not a finished spike.
- **Failed thresholds trigger the named rescope, not silent acceptance** — the
  no-silent-caps discipline: every miss routes to the §2/§3 rescope it's bound to.
