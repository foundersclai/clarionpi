# PI Agent — Feature List

- **Status:** DRAFT for founder review · **Date:** 2026-07-03
- Tiers: **MVP** (first pilot demand), **v1.x** (fast-follow, pre-GA), **v2** (post-demand lifecycle).
- Each feature: one-line description + acceptance one-liner. Milestone mapping in
  [05_implementation_plan.md](./05_implementation_plan.md).

## A. Intake & Corpus (Phase 0)

| # | Feature | Tier | Acceptance |
|---|---|---|---|
| A1 | Matter creation (client, incident, jurisdiction, claim type) | MVP | Matter persists; SOL/notice clock candidates computed on save |
| A2 | Bulk document upload (drag-drop, 100s of PDFs, resumable) | MVP | 1 GB case file uploads without babysitting; failures resumable |
| A3 | Document classification (medical record, bill, police report, wage doc, photo, insurance corr.) | MVP | ≥95% class accuracy on fixtures; misclass fixable in UI |
| A4 | Text-layer fast path + OCR fallback per page | MVP | ≥98% pages yield text; per-page confidence stored |
| A5 | Page store with provenance sidecar (doc, page, image ref, text, confidence) | MVP | Every downstream fact can anchor to a live page |
| A6 | Duplicate detection (same record set uploaded twice; page-hash + fuzzy) | MVP | Dupes quarantined, not silently merged or double-counted |
| A7 | Low-confidence page review queue | v1.x | Pages under threshold flagged for human read |
| A8 | Intake from case-management systems (Filevine/Clio/Litify) | v2 | Documents sync without manual upload |

## B. Extraction & Analysis (Brain-1)

| # | Feature | Tier | Acceptance |
|---|---|---|---|
| B1 | Medical encounter extraction (DOS, provider, facility, complaints, findings, dx/ICD-10, tx/CPT, work status) with page anchors | MVP | ≥95% encounter recall vs gold fixtures; every encounter ≥1 anchor |
| B2 | Encounter merge (same visit across record pulls) | MVP | No duplicate chronology rows on fixture matters |
| B3 | Billing-line extraction (provider, DOS, CPT, billed/adjusted/paid) with page anchors | MVP | Ledger totals reconcile to gold fixtures to the penny |
| B4 | Specials ledger engine (pure code: categories, rollups, billed-vs-paid per jurisdiction) | MVP | Property-tested; LLM never computes a total |
| B5 | Medical chronology builder (deterministic assembly + tokenized per-encounter narratives) | MVP | Every narrative claim resolves to registry facts; editable at G2a |
| B6 | Incident facts extraction from police/incident report | MVP | Parties, citations issued, narrative, diagram page anchored |
| B7 | Risk-flag engine (treatment gaps, pre-existing, degenerative findings, prior claims, causation ambiguity, low property damage) | MVP | Flags carry anchors + severity; G2a blocks until high-severity flags dispositioned |
| B8 | Lost-wages module (wage docs → economic damages, deterministic) | v1.x | Wage loss computed from anchored inputs; attorney-confirmed assumptions |
| B9 | Comparable verdicts/settlements retrieval (pgvector + structured filters: venue, injury, surgery) | v1.x | Suggestions ranked; only attorney-picked comparables reach the letter |
| B10 | Firm private comparables (their closed cases as corpus) | v1.x | Firm-scoped isolation; informs valuation range |
| B11 | Future-care / life-care-plan support | v2 | Future medicals enter ledger only via attorney-confirmed inputs |

## C. Gates & HITL

| # | Feature | Tier | Acceptance |
|---|---|---|---|
| C1 | Gate state machine with audit (`GateRecord`: actor, role, action, payload hash, overrides) | MVP | Every transition attributable; overrides logged with reason |
| C2 | G1 Facts & deadlines review (incident facts, coverage, SOL + notice-of-claim confirmation) | MVP | SOL/notice dates cannot be auto-silenced; attorney confirm required |
| C3 | G1.5 Strategy intake (liability theory, injury framing, emphasis notes, anchor amount, venue posture) | MVP | Structured inputs feed Brain-2 verbatim as attorney signal |
| C4 | G2a Evidence review (chronology edit, exhibit include/exclude per page, risk-flag disposition, comparables picks) | MVP | Paralegal can prep; attorney confirm required to advance |
| C5 | G2.5 Demand plan review (section plan, demand amount, deadline type) | MVP | Plan is the drafting contract; edits re-emit plan, not prose |
| C6 | G3 Compliance review (deterministic checks + semantic findings; span-patch vs section-regen buckets) | MVP | Unresolved tokens/orphans hard-block; every finding dispositioned |
| C7 | Role model (paralegal / attorney / admin) with gate-level sign-off rules | MVP | Attorney-only gates enforced server-side |
| C8 | Strategy-note preflight (classify attorney notes; flag unusable/contradictory instructions) | v1.x | Severity-graduated diagnostics before Brain-2 runs |
| C9 | Blocked-action UX: allow click, inline legal/authorization reason, no gray-outs | MVP | Foreclosed actions explain themselves; overrides audited |

## D. Strategy & Drafting (Brain-2)

| # | Feature | Tier | Acceptance |
|---|---|---|---|
| D1 | Demand strategy memo (Opus) from plan + G1.5 inputs + registry | MVP | Memo cites only registry facts; stored as matter artifact |
| D2 | Tokenized letter drafting per section (`[[FACT_*]] [[AMT_*]] [[CITE_*]] [[EX_*]]`) | MVP | Zero raw provider names/amounts/citations in LLM output |
| D3 | Deterministic renderer/detokenizer | MVP | Orphan token → sentinel + log; never reaches the wire |
| D4 | Letter assembly to `.docx` | MVP | Opens clean in Word; firm letterhead template slot |
| D5 | Exhibit binder assembly (collated PDF, bookmarks, exhibit index, Bates stamping) | MVP | Page-level include/exclude honored; index matches letter references |
| D6 | Auto-redaction assist (SSNs, third-party-patient pages flagged) | v1.x | Flagged pages require disposition before binder build |
| D7 | Time-limited-demand support (per-state statutory requirements as rules) | v1.x | Statutory required terms present when rule active (lawyer-audited YAML) |
| D8 | Tone/length presets per firm | v2 | Firm style captured once, applied per letter |

## E. Provenance & Trust

| # | Feature | Tier | Acceptance |
|---|---|---|---|
| E1 | Fact registry (typed facts: kind, value, anchors, verified status, source) | MVP | Single namespace; G3 blocks unverified facts |
| E2 | Provenance viewer (click fact in letter/chronology → source page with highlight) | MVP | Round-trip works for 100% of rendered facts |
| E3 | Anchor integrity checks (page exists, doc not superseded) | MVP | Broken anchors fail G3, not render time |
| E4 | Provenance report (per-demand audit artifact for the file) | MVP *(promoted from v1.x 2026-07-03 — it is the positioning, per [06](./06_competitive_landscape.md))* | One-click export: every fact + its source pages |

## F. Rules & Intelligence

| # | Feature | Tier | Acceptance |
|---|---|---|---|
| F1 | Jurisdiction rules YAML v1: SOL, notice-of-claim, comparative fault regime, billed-vs-paid | MVP (launch states only) | Lawyer-audited YAML; engine consumes, never hardcodes |
| F2 | Rules-first + LLM-fallback routing (HybridEngine port) for letter-structure decisions | v1.x | YAML hit = deterministic; fallback logged with diagnostic kind |
| F3 | Damages caps + PIP/no-fault + UM/UIM rules | v1.x | Rules surface as gate warnings, not silent behavior |
| F4 | Carrier/adjuster intelligence (response patterns re-rank emphasis) | v2 | n≥5 per carrier else general trends (TM examiner-intel discipline) |
| F5 | In-matter assistant (read-only Q&A over corpus + state, page-cited answers) | v1.x | Cannot mutate state; answers carry anchors |

## G. Platform

| # | Feature | Tier | Acceptance |
|---|---|---|---|
| G1 | Auth + firm tenancy + roles | MVP | Firm-scoped isolation tested; no cross-tenant reads |
| G2 | Per-matter LLM/OCR cost metering + budget caps (ON by default) | MVP | Every call logged to ledger; cap warning at 80%, hard stop configurable |
| G3 | Audit log (append-only) for gates, overrides, artifact builds, PHI access | MVP | HIPAA access-log requirement satisfied |
| G4 | SSE streaming for long phases (Phase 0, analysis, drafting) with typed events | MVP | No internal-reasoning events on the wire |
| G5 | Telemetry: per-matter run logs (agent/research/rules logs, TM `AgentRunLogger` pattern) | MVP | Grading/debug reads logs first (grade-against-logs discipline) |
| G6 | Eval harness: Tier 1 deterministic, Tier 1.5 LLM-rubric, Tier 2 SME review | MVP (Tier 1) / v1.x (1.5, 2) | CI runs Tier 1 on golden fixtures |
| G7 | PHI-scrubbed fixture pipeline (safe-harbor de-identification + manual pass) | MVP | No live PHI in repo or CI |
| G8 | Matter data retention/deletion policy + export | v1.x | Full matter export; deletion honors legal holds |

## H. Post-demand lifecycle (v2 horizon)

| # | Feature | Tier |
|---|---|---|
| H1 | Negotiation tracker (offers/counters timeline, adjuster correspondence analysis, response-letter drafting) | v2 |
| H2 | Lien tracker (health insurance, Medicare/Medicaid, LOP providers) + resolution worksheets | v2 |
| H3 | Policy-limits analysis + bad-faith setup posture (rules-gated, attorney-driven) | v2 |
| H4 | Litigation escalation: complaint draft + templated discovery from the same fact registry | v2 |
| H5 | Settlement disbursement statement generator (deterministic math) | v2 |
| H6 | Treatment monitoring & client check-ins (gap intervention *during* treatment — EvenUp Medical Management analog; natural fit under the captive-firm model, [07](./07_captive_firm_model.md)) | v2 |

**MVP count:** 38 features. The MVP line is drawn so one pilot firm can run a real MVA
demand end-to-end with zero unanchored facts — nothing more (scope-reduction-over-plan-
expansion discipline).
