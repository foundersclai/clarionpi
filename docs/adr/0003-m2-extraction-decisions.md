# ADR-0003: M2 extraction, registry, and ledger implementation decisions

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** foundersclai

## Context

M2 lands the path from an ingested page store to anchored typed facts, a versioned fact registry,
and a specials ledger: window-by-window extraction (`app/corpus/extraction`), encounter merge, the
`[[FACT/AMT]]` token spine (`app/engine/tokenizer`), the pure specials ledger with dedup exclusion
(`app/money`), and the composition of all of these into Phase 0
(`app/corpus/ingest/phase0.py`) — a doc now extracts after it is paged, then a post-loop sync stage
merges encounters and mints tokens/amounts. Several choices along that path are expensive to
reverse or set a boundary a later milestone depends on, so they are recorded here rather than left
implicit. Each keeps M2 shippable, offline-testable, and fail-visible while naming the heavier
decision it defers.

## Decision

We will adopt the following five decisions for M2 extraction/registry/ledger.

1. **Billing reconciliation is `llm_only` before S1.** Every `BillingLine` M2 emits carries
   `reconciliation = llm_only` — a single Sonnet read over the bill. The deterministic
   table-vs-LLM pair (`table_only` / `table_llm_agree` / `table_llm_diff`) is deferred to the S1
   OCR-vendor decision, when a real table read exists to reconcile against. The
   `ReconciliationStatus` enum already carries the future values, so adding the pair is data +
   logic, not a schema change.
2. **Dedup exclusion is document-level, including pending duplicates; page-level is deferred.**
   The specials ledger excludes a document that is `superseded`, or `duplicate_of` and not
   resolved `kept` (an unresolved `pending` exact duplicate does **not** sum; resolving it `kept`
   re-includes it). `partial_overlap` documents are **included** — their unique pages are real
   money. Page-level overlap refinement (summing only the non-overlapping pages of a
   `partial_overlap` doc) is deferred; the exclusion runs before any rollup, so double-counting is
   structurally impossible at the document grain today.
3. **`[[AMT]]` tokens are `source=EXTRACTOR` with hash-drift re-verification, not a status flip.**
   Amounts trace to extracted billing lines, so their token source is pinned `EXTRACTOR` (the
   `extractor|attorney|rules` vocabulary has no `ledger` member and `EXTRACTOR` is the true
   provenance). An AMT stores the ledger value + `ledger_ref` + `ledger_hash`; drift is caught by
   **re-hashing `ledger_ref` at render** (`resolve_for_render` → `amt_mismatch`), never by mutating
   the stored value or flipping a status. The stored number is a stable snapshot; the live ledger
   is the check.
4. **Registry sync runs inside Phase 0.** The Phase-0 sync stage (after the per-doc loop) calls
   `merge_encounters` → `sync_extracted_facts` → `mint_amounts`, so a matter reaching
   `facts_review` already has a populated registry + ledger. The gate's guardless
   `corpus_processing → facts_review` edge absorbs the `REGISTRY_BUMPED` self-loop (the sync bumps
   the registry version as a side effect of the same run, not a separate gated event). Whether
   registry sync belongs to Phase 0 at all — versus a dedicated analysis run that owns the
   registry — is **reconsidered at M3**, when the analysis re-run wave defines its own registry
   ownership; M2 keeps it in Phase 0 so the facts_review gate is never reached with an empty
   registry.
5. **Encounter narratives persist on `MedicalEncounter`, with chronology as the single writer.**
   The tokens-only narrative field lives on the `MedicalEncounter` row
   (`narrative_tokenized`); the extractor leaves it empty and the chronology builder
   (`app/engine/brain1`) is the sole writer of it. This ADR records the decision; the brain1
   contract references the behavior. Keeping one writer avoids two components racing to author the
   same field and keeps the narrative rebuildable from the anchored encounter.

## Consequences

- Extraction → registry → ledger is end-to-end runnable and testable offline at M2 (scripted
  provider for classify + extractors + merge tiebreak; the no-LLM `null` path degrades to review
  and still completes the run + syncs the ledger).
- Each decision names its later counterpart (S1 table reconciliation, M3 registry-ownership
  reconsideration, page-level dedup refinement) so the deferral is traceable, not silent.
- Registry sync inside Phase 0 means a late-document run re-syncs the whole registry/ledger every
  time — correct but not incremental; the M3 analysis wave is where incremental re-sync (if worth
  it) would land.

## Alternatives Considered

- **Wire the table-vs-LLM reconciliation pair now** — rejected: no deterministic table read exists
  until the S1 OCR vendor is chosen, so the three table statuses would be unreachable. *Rollback:*
  add the Textract (or chosen-vendor) table read + the agree/diff comparison behind
  `reconciliation`, populating the existing enum values.
- **Page-level dedup exclusion at M2** — rejected: premature without real overlapping record sets,
  and document-level exclusion already makes double-counting structurally impossible at the doc
  grain. *Rollback:* refine `assemble._excluded_doc_ids` to a page-set exclusion once
  `partial_overlap` page maps exist.
- **A dedicated `ledger` token source + a status flip on drift** — rejected: `EXTRACTOR` is the
  honest provenance and a stored value that mutates on drift loses the snapshot the demand was
  approved against. *Rollback:* add a `ledger` `TokenSource` member and change
  `resolve_for_render` to rewrite the stored value if the ledger-as-truth model is ever preferred.
- **Run registry/ledger sync in a separate analysis run, not Phase 0** — rejected for M2: it would
  let a matter reach `facts_review` with no registry, and there is no analysis run yet (M3).
  *Rollback:* move the sync-stage body into the M3 analysis run and have Phase 0 stop at the gate,
  once analysis owns the registry version.
- **Extractor writes the narrative** — rejected: two writers (extractor + chronology) racing the
  same field, and the narrative is a derived chronology artifact, not an extraction output.
  *Rollback:* move narrative authoring into the extractor if chronology is ever folded back into
  extraction.
