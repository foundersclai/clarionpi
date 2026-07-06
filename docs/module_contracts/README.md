# Module Contracts

Module contracts are short, human-authored **boundary documents** — one per
module where ownership drift would create product, legal, provenance, money,
tenancy, or migration risk. Each is one page: what the module **owns**, what it
**must not own**, what crosses its boundary (owns / consumes / produces), which
[`system_contract.md`](../system_contract.md) invariants it enforces, its key
**vocabulary**, and the **change rule** that says when a PR must update it.

They are not AST dumps and not full API references. At **M0** most modules are
package stubs; each contract's **Status** line states truthfully whether the
module has real code today or lands at a later milestone.

## Index

| Module | Contract | Design source (TMEPAgent repo) |
|---|---|---|
| `backend/app/corpus` (ingest) | [app.corpus.ingest](app.corpus.ingest.md) | `backlog/pi/components/corpus_ingest.md` |
| `backend/app/corpus` (extraction) | [app.corpus.extraction](app.corpus.extraction.md) | `backlog/pi/components/corpus_extraction.md` |
| `backend/app/engine/orchestrator` | [app.engine.orchestrator](app.engine.orchestrator.md) | `backlog/pi/components/orchestrator_gates.md` |
| `backend/app/engine/brain2` | [app.engine.brain2](app.engine.brain2.md) | `backlog/pi/components/brain2_drafting.md` |
| `backend/app/engine/compliance` | [app.engine.compliance](app.engine.compliance.md) | `backlog/pi/components/compliance_engine.md` |
| `backend/app/engine/tokenizer` | [app.engine.tokenizer](app.engine.tokenizer.md) | `backlog/pi/components/fact_registry.md` |
| `backend/app/engine/brain1` | [app.engine.brain1.chronology](app.engine.brain1.chronology.md) | `backlog/pi/components/chronology_builder.md` |
| `backend/app/rules` | [app.rules.jurisdiction](app.rules.jurisdiction.md) | `backlog/pi/components/jurisdiction_rules.md` |
| `backend/app/money` | [app.money.ledger](app.money.ledger.md) | `backlog/pi/components/money_engine.md` |
| `backend/app/package` | [app.package.builder](app.package.builder.md) | `backlog/pi/components/package_builder.md` |
| `backend/app/api` | [app.api.view_models](app.api.view_models.md) | `backlog/pi/components/api_and_wire.md` |
| `backend/app/core` (telemetry) | [app.core.llm_telemetry](app.core.llm_telemetry.md) | `backlog/pi/components/platform_core.md` |
| `backend/app/core` (budget) | [app.core.matter_budget](app.core.matter_budget.md) | `backlog/pi/components/platform_core.md` |

`platform_core` is split into two contracts — **telemetry** (the ledger + the
single metered door) and **matter_budget** (the caps + warnings gate) — that
cross-link each other; the rest of `app/core`'s surface (tenancy, audit, auth)
gets a shared **Cross-cutting** paragraph inside both. `corpus` and `core` each
back two contracts (a module path may legitimately appear twice — the boundaries
differ, the directory is shared).

## How these relate to the rest of the contract stack

- [`system_contract.md`](../system_contract.md) is the **top-level contract** —
  the 14 invariants and the update rule. Each module contract's *Invariants
  enforced* section refers back into it by number; the two change together.
- **This folder** refines those invariants to a **per-module boundary** — the
  granularity at which a PR author decides "does my change move a boundary?"
- [`CONTRACTS.md`](../../CONTRACTS.md) is the **drift matrix**: one row per module
  path → its contract doc. `make hub-check` parses that table and **fails the
  build** if any listed module path or contract doc goes missing — so the matrix,
  these docs, and the filesystem can never silently drift apart.

To add or change a contract, follow the **contract-first-change** workflow in
[`system_contract.md`](../system_contract.md#contract-change-workflow): the
boundary change, the module contract, the `system_contract.md` invariant, and the
tests land in the **same PR**.
