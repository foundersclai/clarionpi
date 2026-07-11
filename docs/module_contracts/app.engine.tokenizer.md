# app.engine.tokenizer

Backs [`system_contract.md`](../system_contract.md) invariants **2, 5, 10, 11**.
Module path: `backend/app/engine/tokenizer`.
Design source: [`backlog/pi/components/fact_registry.md`](../../backlog/pi/components/fact_registry.md).

## Status

**Live @ M2 (2026-07-06).** The registry is implemented and tested in
`backend/app/engine/tokenizer/registry.py`: token grammar (`token_str`/
`parse_token`/`TOKEN_RE`/`SENTINEL`), versioning (`current_version`/`bump_version`
with `RegistryVersion` lineage), minting (`sync_extracted_facts` for encounters +
the incident row, `mint_amounts` for ledger AMTs, `mint_attorney_fact`), and
two-mode resolution (`resolve_for_prompt` = display-form only; `resolve_for_render`
= value + anchors + integrity; `resolve_text_for_wire`/`scan_unregistered`). Phase
0 calls `sync_extracted_facts` + `mint_amounts` in its sync stage. The G2a freeze
and the compliance panel that consumes render resolution land later (M4/M5).

## Responsibility

**The spine.** One **per-matter namespace** of typed facts — `[[FACT_n]]`,
`[[AMT_n]]`, `[[CITE_n]]`, `[[EX_n]]` — each carrying `value`, `display_form`,
anchors, verification status, and source (`extractor | attorney | rules`). This is
the **only minter of tokens** and the single resolution authority: it answers
*token → display form* for Brain-2 prompts (fabrication-safe) and *token → value +
anchors* for the renderer, provenance viewer, and compliance panel. It is
**versioned** (`registry_version` bumps on any post-freeze fact change; G2.5/G3
approvals bind to a version) and **freezes at G2a confirm**.

**Not responsible for:** *computing* values (`app.money.ledger` owns arithmetic;
the registry only *stores* the `[[AMT]]` result + `ledger_hash`); deciding what
enters the letter (attorney gates); extracting facts (`app.corpus.extraction`).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `FactToken` (the versioned registry) + `RegistryVersion` | — |
| Consumes | anchored `MedicalEncounter` / `IncidentFacts` to tokenize | app.corpus.extraction |
| Consumes | `[[AMT]]` facts (value + `ledger_hash`) | app.money.ledger |
| Consumes | attorney-added facts + dispositions (`source=attorney`) | app.engine.orchestrator (G1/G2a) |
| Consumes | rules-derived facts (deadlines, required terms; `source=rules`) | app.rules.jurisdiction |
| Produces | token → display form (prompt-safe) | app.engine.brain2 |
| Produces | token → resolution (verify + value) | app.engine.compliance |
| Produces | token → value + anchors (render) | app.package.builder |
| Produces | token → anchors (click-through) | app.api.view_models → frontend |

## Invariants enforced

- **[2]** Every token carries anchors; render resolution runs anchor integrity;
  unanchored/broken → block, not wire.
- **[5]** Prompt resolution exposes only `display_form` (Brain-2 never sees raw
  names/cites/amounts); adverse facts are tokens with stance metadata, **one
  namespace** (the deliberate TM doctrine-fit carry-over — not segregated
  legends).
- **[10]** The registry is derived state — rebuildable from extractor rows +
  attorney elections + rules; versioning makes rebuilds addressable. Token ids are
  **stable and never reused across versions** (`FACT_12` is the same fact-slot
  forever).
- **[11]** An orphan (a token nothing resolves) renders as a **sentinel** + loud
  log + a **hard G3 block** — never the raw token, never a guessed value.

## Vocabulary

`FactToken` (`token_id`, `kind`, `value`, `display_form`, `anchors`, `status`,
`source`; AMT-only `ledger_ref`/`snapshot_value_cents`/`ledger_hash`) ·
`RegistryVersion` (`frozen` from G2a; `parent_version`, `change_reason`) ·
`ResolutionResult.outcome` ∈ {`ok`, `orphan`, `amt_mismatch`, `unverified`,
`disputed`} — the landed set: an integrity failure surfaces as `unverified` (a
stale/superseded anchor drops the row's status to `UNVERIFIED` at sync, not a
separate `integrity_fail` outcome), and the blocking semantics the draft called
`unverified_block` live at the G3 gate, not in the resolver. `SENTINEL` =
`"[UNRESOLVED FACT]"` (deliberately **not** token-shaped, so a leaked sentinel
can't be re-parsed as a token). One **shared ordinal namespace** across all four
kinds — the next ordinal is one past the highest ever minted for the matter,
`FACT`/`AMT`/`CITE`/`EX` interleaved. `source_ref` idempotency keys
(`encounter:<id>` / `incident:<id>` / `amt:<ledger key>` / `attorney:<uuid4>`)
make a re-sync resolve to the same slot. Version-bump reasons ∈ {`extraction_sync`,
`ledger_sync`, `attorney_fact`}. `[[AMT]]` source is pinned `EXTRACTOR` (amounts
derive from extracted billing lines; the `extractor|attorney|rules` vocabulary has
no `ledger` member and `EXTRACTOR` is the true provenance). Two resolution modes
(**prompt** = display_form only; **render** = value + anchors + integrity).

## Change rule

A boundary change requiring a contract update: adding a token kind or changing the
single-namespace rule; changing the `FactToken` shape, the versioning/freeze
semantics, or token-id stability; changing the `[[AMT]]` re-verification
(`ledger_hash`) contract with `app.money.ledger`; changing the orphan/sentinel
policy. **Only `app.engine.tokenizer` mints tokens** — a mint anywhere else is a
boundary breach. Update this file **and**
[`system_contract.md`](../system_contract.md) §2/5/10/11 in the same PR.


## BUS-05 addendum (ADR-0012): caller-owned EX settlement

`mint_exhibits` (and `_apply_desired`) accept `commit=False` for caller-owned
transactions: the G2a confirm side effect settles ALL manifest EX tokens INSIDE the
gate-action transaction (settle → cursor advance → freeze, one atomic act). Package
assembly never mints — it consumes settled tokens read-only via the manifest.
