# Extraction confidence roadmap — never let a dropped or unverified number reach the ledger silently

- Track: EC (extraction confidence). New track; no parent roadmap.
- Status: planned (design captured 2026-07-20 from the workshop rebuild session; no slice started).
- Slices: EC-1 → EC-4 below, strictly ordered (each later slice consumes the signal the earlier one
  creates). EC-1 is the prerequisite for everything; EC-2/EC-3 are optimizations that only make
  sense once EC-1's oracle exists.
- Tier expectation: **every slice here touches a Tier-3 trigger** (money, gate guards, or
  prompt/LLM paths — the tier rules fire those at full strength in every environment). None of
  this is quick-fix work; each slice gets its own `sdlc-tier-assessment` + plan before code.
- Related open chip: "Fix bill extractor parse_failed on quantity-priced lines"
  (spawned 2026-07-20) — that is the *bug instance*; this roadmap is the *systemic design* the bug
  exposed. EC-1's regression fixture must include the quantity-priced-line bill either way; the
  prompt/schema fix itself can land via the chip or be folded into EC-2's prompt work.

## Motivating incident (observed, not hypothetical)

During the 2026-07-20 workshop rebuild (live `LLM_PROVIDER=anthropic`, extractor
`claude-sonnet-5`, prompt `bill_v1`):

- `05_ortho_bill.pdf` and `07_pt_bill.pdf` failed extraction **deterministically** (reproduced in
  two independent runs days apart): 2 `extraction_runs` rows each with `error="parse_failed"`,
  `rows_emitted=0`; both bills contain compound quantity-priced line items
  ("Established patient follow-up visits, 3 at $430.00 …… $1,290.00" /
  "Therapeutic exercise, 12 sessions at $295.00 (97110) … $3,540.00"). The flat-line ER bill
  extracted cleanly.
- Consequence: the documents sat at `status="ocr_done"` with **empty `failure_reason`**, absent
  from the specials ledger. The ledger showed $18,750 grand billed instead of the scenario-true
  $29,050 — internally consistent, categories summing cleanly, **no operator-visible signal that
  $10,300 of real charges were missing**.

That last property is the actual defect class this roadmap kills. For a demand package, a silently
*missing* charge is worse than a wrong one: a wrong number can be caught against the page; a
confident-looking total that is quietly short goes out the door.

## Design principle

> The AI never has to be right — it has to be either right or loudly unsure.
> **The forbidden state is silent.**

Three corollaries the slices implement:

1. **Trigger on computed evidence, not model self-confidence.** LLM self-reported confidence is
   miscalibrated (confidently wrong, nervously right). The trustworthy signals are deterministic:
   stated-total reconciliation, parse failures, anchor-validation rejects. Model self-confidence
   may *demote* (flag for review) but never *promote* (mark verified).
2. **Interrupt at the gate, not mid-pipeline.** Corpus processing is an async multi-minute run;
   nobody is watching a modal. The system's rhythm is: stages accumulate flags, gates interrupt.
   Classification already does exactly this (two parse failures → degrade to the review queue,
   `corpus/ingest/classify.py`); extraction gets the same treatment.
3. **Every interrupt ships a repair path.** A blocked attorney with no affordance rubber-stamps or
   rage-quits. Each surfaced failure renders the page (existing provenance viewer) + the computed
   contradiction as caption + inline actions (retry / enter lines / exclude-with-reason).

## Severity model (keeps interrupts rare enough to stay meaningful)

| Failure | Severity | Behavior |
|---|---|---|
| Bill-type document contributes $0 against a stated total | **Blocking at G2a** | approve refused with a typed `guard_failed` code; attorney resolves or overrides with reason (existing `override_required`/`override_reason` machinery) |
| Lines extracted but don't sum to the stated total | Flag | ledger-row badge + required disposition (reuse the risk-flag disposition pattern, `PUT /api/flags/{id}/disposition`) |
| Soft low-confidence field | Non-blocking | existing `unverified` token status / "pending verification" badge |

## Slices

### EC-1 — Stated-total reconciliation + the no-silent-state invariant (the oracle)

Goal: extraction failure becomes impossible to miss, and every bill's extraction is checked
against the bill's own declared total.

- Every bill states its own total ("TOTAL CHARGES: $6,400.00" / "Balance due …"). Capture the
  stated total per bill document (design decision for the slice plan: a document-level field in
  the same extraction pass vs a deterministic text scan of the page — or both, cross-checked) and
  reconcile it against the sum of that document's extracted `billing_lines`. Mismatch or
  zero-lines-vs-nonzero-total → a deterministic, loud discrepancy record.
- **Exhaustiveness invariant:** every document ends in exactly one of
  *extracted / review-queued-with-reason / excluded-with-reason*. The current fourth, silent state
  (`ocr_done`, empty `failure_reason`, absent from the ledger — `corpus/extraction/runner.py`
  ~475-495 records the `parse_failed` run but surfaces nothing doc-level) is made unrepresentable:
  a doc whose windows exhausted retries gets `failure_reason` populated and lands in the operator
  review surface.
- **G2a guard:** approve is guarded (typed `guard_failed` code) while a bill-type document
  reconciles to zero, with the audited override path as the escape hatch.
- Money discipline: reconciliation arithmetic lives in `app/money` (integer cents); the ledger
  stays a pure derived view (inv 10).
- Regression fixtures: a quantity-priced-line bill (the observed failure shape) + a
  stated-total-mismatch bill.

### EC-2 — Informed retry (the free rung)

Goal: recover a large share of parse failures at the same model tier, before any escalation.

- The runner currently makes two *blind* attempts per window. Make the second attempt *informed*:
  feed back the validator error and the EC-1 stated total ("your output failed validation: X; the
  document states TOTAL CHARGES $6,400"). Self-repair with the oracle in the prompt.
- Prompt change ⇒ `prompt_version` bump (`bill_v1` → next); runs already record prompt_version, so
  the audit story is intact.
- Acceptance is EC-1's reconciliation, not "it parsed".

### EC-3 — Model escalation ladder with outcome telemetry

Goal: shrink the human queue to the residue that beat two models.

- Ladder: `sonnet → sonnet+error-feedback (EC-2) → opus → human queue`. Trigger and acceptance are
  both EC-1's computed signals — never model self-confidence.
- Economics (why this is a when-not-if): a failed bill window is ~1–2 pages; an opus retry costs
  cents, an attorney interrupt costs minutes. The threshold "retry-cost < P(resolves) ×
  interrupt-cost" is met immediately; this slice is sequenced third only for engineering focus.
- Plumbing already exists: all calls go through the metered client in `app/core`; `llm_calls`
  meters stage/model/cost; `extraction_runs.model` records which model produced each accepted
  extraction (provenance survives escalation); `matter_budgets` bounds blowup.
- **Escalation fixes capability failures, not contract failures.** Telemetry requirement: when the
  same failure shape escalates repeatedly (same doc pattern, same error), that is a prompt/schema
  bug to fix at the source — the ladder must not become where systematic bugs hide. (The observed
  quantity-priced-line failure is likely a *contract* failure: opus would plausibly fail the same
  schema. This is testable once the chip captures the raw failing output.)

### EC-4 — The human queue as the designed floor

Goal: the interrupt that remains is cheap to clear and impossible to resent.

- The queue card = the failed/discrepant document's **rendered page** (existing provenance/blob
  viewer — the audited-PHI-read machinery) + the computed contradiction as a one-line caption
  ("This bill states $6,400.00 in charges; I captured $0.00.") + three affordances:
  1. **Retry extraction** (re-run the ladder on this doc),
  2. **Enter/correct the lines manually** — wire into the existing G2a billing source-row edits
     (`POST /api/matters/{id}/billing/edits`); hand-entered lines carry `source: attorney` and
     flow into the same ledger → same AMT composition → same provenance drawer (trust story
     intact),
  3. **Exclude with reason** (audited).
- Volume expectation: a real PI file is dozens-to-hundreds of documents; at a few % failure rate
  that is a handful of interrupts per matter. EC-2/EC-3 shrink the queue; EC-4 makes the residue
  survivable. The floor is never removed — starved of volume.

## Explicit non-goals (for the whole track)

- No trusting or displaying raw LLM self-confidence scores as verification.
- No mid-pipeline modal interruptions; the gate surfaces are the only interrupt points.
- No silent zero-fill or billed-substitution anywhere in money (existing discipline; the
  reconciliation surfaces gaps, never patches them).
- No editing the workshop scenario source to dodge the extractor bug — the compound line is
  realistic and the fixture keeps it.

## Honest-pitch framing (product, not code)

The sales claim this track enables is not "our extraction is 99% accurate" (unfalsifiable; one
miss kills trust) but: **"nothing reaches your demand letter without either a page citation or
your own hands on it."**
