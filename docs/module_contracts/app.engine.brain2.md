# app.engine.brain2

Backs [`system_contract.md`](../system_contract.md) invariants **1, 3, 5, 6, 11, 13**.
Module path: `backend/app/engine/brain2`.
Design source: [`backlog/pi/components/brain2_drafting.md`](../../backlog/pi/components/brain2_drafting.md).

## Status

**Stub @ M0, lands M5.** The package exists (empty `__init__.py`, created as
scaffolding). `StrategyPlan` / `PlannedSection` / `StrategyInputs` are modeled in
`app/models`. No memo, drafter, validator, or prompt-assembly logic exists yet.

## Responsibility

Turn attorney-**approved** structure into persuasive **tokenized** prose. Generate
the strategy memo (Opus) and, per planned section, a drafter (Opus) bound by a
`SectionContract` and late-bound hard constraints; **validate every section
deterministically**; regen on failure or on a compliance finding (single-section
scope). Output is **tokens-only** — zero raw provider names, amounts, or citations
reach the wire; rendered previews resolve via the registry.

**Not responsible for:** *what* to argue (attorney at G1.5/G2.5); arithmetic
(references `[[AMT_n]]` only); rendering/detokenization (`app.engine.tokenizer` +
`app.package.builder`); compliance *verdicts* (`app.engine.compliance`).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | memo generation, per-section drafter, section validator, `DrafterPromptSnapshot` | — |
| Owns | `DraftSection` (tokenized section rows — sole producer) | — |
| Consumes | `StrategyPlan` (section contracts, demand) | app.engine.orchestrator (G2.5) |
| Consumes | `StrategyInputs` **verbatim** (G1.5 attorney signal) | matter state |
| Consumes | token → display form | app.engine.tokenizer |
| Consumes | risk dispositions (address list + no-volunteer set) | brain1 (risk) |
| Consumes | statutory required terms (when active) | app.rules.jurisdiction |
| Produces | memo artifact + tokenized `DraftSection[]` | app.package.builder / app.engine.tokenizer |
| Produces | `section` SSE (rendered preview, never tokenized) | app.api.view_models |
| Produces | shared prompt snapshot (judge symmetry) | app.engine.compliance |

## Invariants enforced

- **[1]** Drafting happens only after G2.5 approval; a section that fails
  validation twice **surfaces** (`surfaced_failed`), it never loops to satisfy a
  proxy.
- **[3]** No arithmetic here; dollar figures are `[[AMT_n]]` references.
- **[5]** Tokens-only output; raw names/amounts/citations never leave the drafter;
  an unknown token is a validator reject + one retry, then surface — the drafter
  **never mints on demand**.
- **[6]** Address-list constraints honored; the no-volunteer set is never surfaced.
- **[11]** Rendered previews via registry resolution; tokens never on the wire.
- **[13]** Validation is deterministic code; semantic judgment is the LLM judge's
  (`app.engine.compliance`), never a code-side normalizer patching drafter output.

## Vocabulary

`SectionContract` (`allowed_tokens`, `required_tokens`, `max_words`) ·
`DraftSection` (`body_tokenized` = tokens only; `registry_version`; `validation`
∈ {`passed`, `retry_pending`, `surfaced_failed`}) · `DrafterPromptSnapshot`
(`input_hash` locks drafter↔judge symmetry; `rules_blocks` vs `matter_directives`
vs late-bound `final_hard_constraints`) · **strict single retry per section**.
v1 `[[CITE_n]]` origins are exactly two, both pre-verified (rules-pack statutes +
attorney-supplied authorities); **no LLM-proposed authority in v1**.

## Change rule

A boundary change requiring a contract update: changing the `DraftSection`
tokens-only contract or the `SectionContract` shape; changing the
`DrafterPromptSnapshot` symmetry/hash contract; changing the retry budget or the
rules-blocks-vs-matter-directives layering; adding a `[[CITE_n]]` origin;
emitting anything other than the `section` rendered-preview SSE. Update this file
**and** [`system_contract.md`](../system_contract.md) §1/3/5/6/11/13 in the same PR.
