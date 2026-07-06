# app.money.ledger

Backs [`system_contract.md`](../system_contract.md) invariants **3, 10**.
Module path: `backend/app/money`.
Design source: [`backlog/pi/components/money_engine.md`](../../backlog/pi/components/money_engine.md).

## Status

**Extended @ M2 (2026-07-06).** The currency discipline was already live in the
model layer (`Cents` alias, integer-cents `*_cents` fields,
`matter_budget_default_cents`); M2 adds the specials ledger and `[[AMT]]` emission:

- `ledger.py` — the primitive pure `rollup` (per-category + grand `LedgerColumns`).
- `specials.py` — the specials ledger v2: `line_set_hash` over the exact line set,
  document-level dedup exclusion applied **before any sum**, the jurisdiction
  billed-vs-paid demand basis, and `amounts_for_registry` (the deterministic
  `[[AMT]]` payloads the tokenizer mints).
- `assemble.py` — the one DB-touching layer: reads `BillingLine` rows + dedup
  verdicts, shapes the pure inputs, composes `compute_matter_ledger`.

Phase 0's sync stage calls `compute_matter_ledger` → `amounts_for_registry` →
`registry.mint_amounts`. Nothing here writes a total; the ledger is a derived view.

## Responsibility

**All arithmetic on `Money`.** The specials ledger (a derived view over
`BillingLine`: category rollups; billed / adjusted / paid / outstanding columns;
the jurisdiction billed-vs-paid basis), demand math, wage loss (v1.x), and package
totals. Every function is **pure** — integer cents + currency in, integer cents
out — with a documented **round-half-up** policy applied once at the presentation
boundary. It hands `{value, LedgerRef, ledger_hash}` to `app.engine.tokenizer`,
which mints the `[[AMT_n]]`; the LLM references those tokens and **never computes
a total**.

**Not responsible for:** extraction (`app.corpus.extraction` owns `BillingLine`);
minting tokens (`app.engine.tokenizer` — money hands it values); letter text
(`app.engine.brain2`); fee splits / disbursement (out of scope).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `SpecialsLedger` (derived), rollup + demand-math functions, rounding policy | — |
| Consumes | `BillingLine` rows (billed/adjusted/paid, category, `dedup_status`, anchor) | app.corpus.extraction |
| Consumes | billed-vs-paid basis per jurisdiction | app.rules.jurisdiction |
| Consumes | attorney recategorization (writes back to `BillingLine.category`) | app.engine.orchestrator (G2a) |
| Produces | `[[AMT]]` facts (`value` + `ledger_hash`) | app.engine.tokenizer |
| Produces | ledger grid view-model (categories, columns, totals) | app.api.view_models (G2a) |

### M2 boundaries

- **`SpecialsLedger` is a derived view.** `by_category` + `grand_total`
  (`LedgerColumns`), the `line_set_hash` an `[[AMT]]` pins to, the `basis`, the
  `demand_basis_total_cents`, and the surfaced `excluded_line_ids` /
  `missing_paid_line_ids`. It is always recomputable from the billing-line set — a
  correction is a `BillingLine` edit upstream, never a hand-edit of a total.
- **Exclusion rule (document-level at M2; verbatim).** A `DedupDecision` resolved
  `superseded` excludes its document; a document classified `duplicate_of` is
  excluded **unless** its decision is resolved `kept` — an unresolved (`pending`)
  exact duplicate must **not** sum, and resolving it `kept` re-includes it on the
  next recompute. `partial_overlap` documents are **included** (their unique pages
  are real money; the overlapping pages are a page-level refinement deferred). All
  exclusion happens **before any rollup**, so double-counting is structurally
  impossible, not filtered after the fact.
- **Basis is rules-owned, `billed` default.** `pack.billed_vs_paid_basis` supplies
  `billed` or `paid`; a pack without the block falls back to `billed` (the
  conservative default — it never silently understates a demand by substituting
  paid where paid is absent). An unknown basis is a typed `ValueError`, not a
  fallback.
- **Missing-paid is surfaced, never billed-substituted.** Under `paid` basis, a
  line with `paid is None` is listed in `missing_paid_line_ids` and left out of the
  demand-basis sum — substituting billed on a paid-basis jurisdiction is a legal
  call code must not make. `None` (no data) is kept distinct from `0` in the hash.
- **AMT key vocabulary (evals + tokenizer spec against these exact strings).**
  `specials.grand.billed` (always) · `specials.grand.paid` /
  `specials.grand.outstanding` (each emitted only when that column's grand sum is
  non-zero) · `specials.category.{category}.billed` (one per present category) ·
  `specials.demand_basis` (always). Every payload's `ledger_hash` is
  `line_set_hash`; each category ref carries exactly that category's included line
  ids.

## Invariants enforced

- **[3]** Every total is a pure function of `BillingLine` rows; the only way to
  change a number is to edit an anchored source row — the ledger is never
  hand-edited. Lines whose document is `duplicate_of`/`superseded` are **excluded
  from the id-set before any rollup** (see the exclusion rule above), so
  double-counting is structurally impossible, not filtered after the fact. The
  `[[AMT]]` the registry stores is a snapshot pinned to `line_set_hash`; drift is
  caught by re-hashing at render, not by mutating a stored value.
- **[10]** The ledger is a **derived view** — always recomputable from
  `BillingLine`; materialization on `line_set_hash` is cache, not truth. The same
  hash is what an `[[AMT]]` snapshots, so drift is detectable downstream.

## Vocabulary

Integer `Cents` (no floats, ever) · `LedgerCategory` (the fixed v1 eight-category
taxonomy; a new category is a code change) · `LedgerColumns`
(`billed`/`adjusted`/`paid`/`outstanding`) · `LedgerLine` (the frozen carrier
`assemble` shapes from a `BillingLine` row, its `document_id` parsed out of the
anchor) · `SpecialsLedger` (`line_set_hash`, `by_category`, `grand_total`, `basis`,
`demand_basis_total_cents`, `included_line_ids`, `excluded_line_ids`,
`missing_paid_line_ids`, `category_line_ids`) · `AmountFact` (`key`, `value_cents`,
`display_form`, `ledger_ref = {line_ids, category, column}`, `ledger_hash`) ·
billed-vs-paid **basis** (`billed` | `paid`, rules-owned) · rounding = `half_up`,
once at presentation.

## Change rule

A boundary change requiring a contract update: changing the `Money`
representation or the rounding policy; changing the `LedgerCategory` taxonomy;
changing the `line_set_hash` definition or the `LedgerRef` shape handed to the
tokenizer; changing the billed-vs-paid basis contract; adding a demand-math input
(e.g. wage loss). **Only `app/money` performs arithmetic on `Money`** — a total
computed anywhere else is a boundary breach. Update this file **and**
[`system_contract.md`](../system_contract.md) §3/10 in the same PR.
