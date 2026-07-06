# app.money.ledger

Backs [`system_contract.md`](../system_contract.md) invariants **3, 10**.
Module path: `backend/app/money`.
Design source: [`backlog/pi/components/money_engine.md`](../../backlog/pi/components/money_engine.md).

## Status

**Stub @ M0, lands M2.** The package exists; the currency discipline is already
live in the model layer (`Cents` alias in `app/models/schemas.py`, integer-cents
`*_cents` fields on `BillingLine`/`StrategyPlan`/`MatterBudget`,
`matter_budget_default_cents` in `app/core/config.py`). The ledger rollup/demand-
math functions and `[[AMT]]` emission are not yet implemented.

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

## Invariants enforced

- **[3]** Every total is a pure function of `BillingLine` rows; the only way to
  change a number is to edit an anchored source row — the ledger is never
  hand-edited. Lines whose page is `duplicate_of`/`superseded` are **excluded
  from the id-set before any rollup**, so double-counting is structurally
  impossible, not filtered after the fact.
- **[10]** The ledger is a **derived view** — always recomputable from
  `BillingLine`; materialization on `line_set_hash` is cache, not truth. The same
  hash is what an `[[AMT]]` snapshots, so drift is detectable downstream.

## Vocabulary

`Money = (cents: int, currency: str)` (no floats, ever) · `LedgerCategory` (the
fixed v1 eight-category taxonomy; a new category is a code change) ·
`LedgerColumns` (`billed`/`adjusted`/`paid`/`outstanding`) · `SpecialsLedger`
(`line_set_hash`, `by_category`, `grand_total`, `billed_vs_paid_basis`,
`demand_basis_total`) · `LedgerRef` (`line_set`, `category`) · rounding =
`half_up`, once at presentation.

## Change rule

A boundary change requiring a contract update: changing the `Money`
representation or the rounding policy; changing the `LedgerCategory` taxonomy;
changing the `line_set_hash` definition or the `LedgerRef` shape handed to the
tokenizer; changing the billed-vs-paid basis contract; adding a demand-math input
(e.g. wage loss). **Only `app/money` performs arithmetic on `Money`** — a total
computed anywhere else is a boundary breach. Update this file **and**
[`system_contract.md`](../system_contract.md) §3/10 in the same PR.
