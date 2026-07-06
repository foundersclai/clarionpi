# app.corpus.extraction

Backs [`system_contract.md`](../system_contract.md) invariants **2, 5, 13**.
Module path: `backend/app/corpus`.
Design source: [`backlog/pi/components/corpus_extraction.md`](../../backlog/pi/components/corpus_extraction.md).

## Status

**Stub @ M0, lands M2.** `app/corpus` is a package stub. The `MedicalEncounter` /
`BillingLine` models and `PageAnchor` are in `app/models`. No windowing,
extraction, anchor-validation, reconciliation, or merge logic exists yet.

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

## Invariants enforced

- **[2]** Schema-level non-empty anchors on every row; each anchor's `(doc, page)`
  **must lie inside** the `window_pages` of the run that produced it — a
  fabricated (out-of-window) anchor is rejected before it persists.
- **[5]** Emits the raw typed facts `app.engine.tokenizer` tokenizes; provider
  names / diagnoses / amounts exist here as data, entering prompts only as tokens.
- **[13]** Only **mechanical** post-parse normalization (dates → `date`, money →
  integer cents, whitespace, code mapping) — **no semantic rewriting** of
  diagnoses/findings; a Textract-vs-extractor cell diff surfaces as
  `table+llm_diff` at G2a, never silently picked.

## Vocabulary

`PageAnchor` (`doc_id`, `page`, `bbox?`, `window_id`, `field?`) · `ExtractionRun`
(idempotency key `(document, window, prompt_version)`; `window_pages`;
`ok`/`partial`/`failed`) · `MedicalEncounter.merge_basis`
(`deterministic_key`/`llm_tiebreak`) + `merged_from` (provenance-preserving,
reversible) · `BillingLine.reconciliation`
(`table_only`/`table+llm_agree`/`table+llm_diff`).

## Change rule

A boundary change requiring a contract update: changing the anchor-window
validation rule; changing the `MedicalEncounter`/`BillingLine`/`IncidentFacts`
shape crossing to the registry or the ledger; changing the merge key/tiebreak
contract; changing the reconciliation states; adding a code-side normalizer over
LLM output (forbidden by §13). Update this file **and**
[`system_contract.md`](../system_contract.md) §2/5/13 in the same PR.
