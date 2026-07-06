# app.corpus.extraction

Backs [`system_contract.md`](../system_contract.md) invariants **2, 5, 13**.
Module path: `backend/app/corpus`.
Design source: [`backlog/pi/components/corpus_extraction.md`](../../backlog/pi/components/corpus_extraction.md).

## Status

**Live @ M2 (2026-07-06).** Extraction is implemented and tested under
`backend/app/corpus/extraction/`:

- `windows.py` — page windowing: overlapping, `[PAGE n]`-prefixed absolute-page
  excerpts (`build_windows`, size/overlap config-driven).
- `prompts.py` — per-kind prompt builders (`medical`/`bill`/`police`) + the
  `PROMPT_VERSIONS` registry that pins each kind's prompt version.
- `runner.py` — `extract_document`: window-by-window, metered, anchor-validated
  persistence into `MedicalEncounter` / `BillingLine` / `IncidentFacts` with
  per-window `ExtractionRun` idempotency.
- `merge.py` — `merge_encounters`: deterministic exact-key collapse first, LLM
  tiebreak for near-matches only.

The runner is composed into Phase 0 (`app/corpus/ingest/phase0.py`): an
`ocr_done`/freshly-paged doc extracts, then the post-loop sync stage merges
encounters and hands facts to `app.engine.tokenizer` + `app.money.ledger`.
Reconciliation is `llm_only` at M2 (the table-vs-LLM pair lands with S1).

## Responsibility

Turn `DocumentPage` rows into **typed, page-anchored, deterministically-
normalized facts**: `MedicalEncounter` (with cross-pull merge), `BillingLine`
(OCR table output reconciled by the Sonnet extractor), and per-matter
`IncidentFacts`. Every emitted row/field carries **≥1 anchor validated against
the exact prompt window it came from** — an anchor citing a page the model never
saw is *rejected* (anti-fabrication). Extraction turns raw records into structure;
it does **no arithmetic, no chronology assembly, no token minting**.

**Not responsible for:** chronology assembly (`app/engine/brain1/chronology`);
`Money` arithmetic or rollups (`app.money.ledger`); minting `[[FACT/AMT]]` tokens
(`app.engine.tokenizer`); deciding what enters the letter (attorney gates).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `MedicalEncounter`, `BillingLine`, `IncidentFacts`, `ExtractionRun` | — |
| Consumes | `DocumentPage` rows (active text, image ref, confidence, `zero_text`) | app.corpus.ingest |
| Consumes | OCR table structures (bill grids) | app.corpus.ingest / external OCR |
| Produces | anchored `MedicalEncounter` / `IncidentFacts` rows | app.engine.tokenizer, brain1 (chronology, risk) |
| Produces | anchored `BillingLine` rows | app.money.ledger (ledger source of truth) |
| Produces | `status` SSE (per-doc extraction progress, gaps) | app.api.view_models → frontend |

### M2 boundaries

- **Reconciliation is `llm_only` until S1.** Every `BillingLine` M2 emits carries
  `reconciliation = llm_only` (a single Sonnet read over the bill). The
  table-vs-LLM pair (`table_only`/`table_llm_agree`/`table_llm_diff`) lands with
  the S1 OCR-vendor decision, when a deterministic table read exists to reconcile
  against — the enum already carries the future values.
- **Window size/overlap are config.** `extraction_window_pages` (8) /
  `extraction_window_overlap` (2) in `core/config.py`; consecutive windows share
  `overlap` pages so a straddling record lands whole in ≥1 window. Overlap must be
  `< size` or `build_windows` raises.
- **Anchor-in-window rejection is per-row, drop-not-guess.** An emitted row whose
  cited page(s) fall outside `[window.start_page, window.end_page]` is dropped
  whole and counted in `anchors_rejected` — never persisted, never repaired. A
  money string the money engine refuses is a separate drop (`rows_dropped_unparseable`).
- **Doc-level EXTRACTED rule.** A doc reaches `extracted` only if **every** window
  ran `ok` **or** `partial`; any `failed`/missing window leaves it `ocr_done`
  (resumable). A provider/budget outage records the current window `failed` and
  stops, leaving no run rows for later windows so a re-run resumes exactly there.
- **Merge reversibility via `merged_from`.** Before an absorbed encounter is
  deleted, a full JSON snapshot of its business fields is appended to the
  survivor's `merged_from` — the merge is reversible by construction. Near-match
  pairs the tiebreak model can't reach (absent/unavailable/unparseable) are left
  **unmerged and counted** (`tiebreaks_skipped`), never guessed in code.
- **Prompt-version vocabulary.** `PROMPT_VERSIONS` (`med_v1`/`bill_v1`/`pol_v1`)
  is part of the `ExtractionRun` idempotency key `(document, window,
  prompt_version)`; bumping a version is the sanctioned way to force a
  re-extraction of every window (S2 prompt iteration).

## Invariants enforced

- **[2]** Schema-level non-empty anchors on every row; each anchor's `(doc, page)`
  **must lie inside** the window span of the run that produced it — a fabricated
  (out-of-window) anchor is rejected before it persists (`_anchors_in_window` in
  `runner.py`).
- **[5]** Emits the raw typed facts `app.engine.tokenizer` tokenizes; provider
  names / diagnoses / amounts exist here as data, entering prompts only as tokens.
- **[13]** Only **mechanical** post-parse normalization (dates → `date`, money →
  integer cents via `app.money.types.dollars_str_to_cents`, whitespace) — **no
  semantic rewriting** of diagnoses/findings. Merge is key-first (deterministic),
  with the LLM tiebreak scoped to genuine near-matches only; a future
  Textract-vs-extractor cell diff surfaces as `table_llm_diff` at G2a, never
  silently picked.

## Vocabulary

`PageAnchor` (`document_id`, `page`, `bbox?`, `window_id`, `field?` — the M1
stored shape reused for M2 emission; `window_id` is the anti-fabrication target) ·
`Window` (`window_id = "{document_id}:{start}-{end}"`; inclusive 1-based absolute
span) · `PROMPT_VERSIONS` (`med_v1`/`bill_v1`/`pol_v1`) · `ExtractionRun`
(idempotency key `(document, window, prompt_version)`; `window_start`/`window_end`;
status `ok`/`partial`/`failed`) · `ExtractionOutcome` (`runs_ok`/`runs_partial`/
`runs_failed`, `rows_emitted`, `anchors_rejected`, `rows_dropped_unparseable`,
`skipped_reason` ∈ {`doc_type_not_extractable`, `no_pages`, `None`}) ·
`MergeOutcome` (`merged_groups`, `llm_tiebreaks`, `encounters_remaining`,
`tiebreaks_skipped`) · `MedicalEncounter.merge_basis`
(`deterministic_key`/`llm_tiebreak`) + `merged_from` (provenance-preserving,
reversible) · `BillingLine.reconciliation` — M2 emits `llm_only`; the S1 pair
`table_only`/`table_llm_agree`/`table_llm_diff` is defined, unused.

## Change rule

A boundary change requiring a contract update: changing the anchor-window
validation rule; changing the `MedicalEncounter`/`BillingLine`/`IncidentFacts`
shape crossing to the registry or the ledger; changing the merge key/tiebreak
contract; changing the reconciliation states; adding a code-side normalizer over
LLM output (forbidden by §13). Update this file **and**
[`system_contract.md`](../system_contract.md) §2/5/13 in the same PR.
