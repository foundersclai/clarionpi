# app.engine.brain2

Backs [`system_contract.md`](../system_contract.md) invariants **1, 3, 5, 6, 11, 13**.
Module path: `backend/app/engine/brain2`.
Design source: [`backlog/pi/components/brain2_drafting.md`](../../backlog/pi/components/brain2_drafting.md).

## Status

**Live @ M5.** The package is implemented + tested: `plan.py` (deterministic section
skeleton + token allocator + the Opus emphasis pass), `memo.py` (the strategy memo),
`drafter.py` (the per-section Opus drafter + the `DrafterPromptSnapshot`),
`validator.py` (the deterministic FORM validator), `renderer.py` (tokenized body →
rendered preview + char-offset spans), `constraints.py` (the late-bound
`HardConstraintInputs`), and `generate.py` (the `drafting -> compliance_review` SSE
run). Wired on the wire by `app/api/routes/drafting.py` (plan emit + demand generate)
and consumed by the G3 panel (`app.engine.compliance`) via the shared snapshot. The
G2.5 plan-approve pin lives in `app.engine.orchestrator` (the `_approve_plan_version`
side effect); the M5-exit E2E (`tests/api/test_m5_exit_flow.py`) drives the full arc.
Decisions recorded in [ADR-0007](../adr/0007-m5-drafting-decisions.md).

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

**Stage ids** (on the metering ledger): `plan.emphasis` (the emit's emphasis pass) ·
`draft.memo` (the strategy memo) · `draft.section` (the per-section drafter). The
judge's `compliance.judge` belongs to `app.engine.compliance`.

**Plan allocator (deterministic, inv 5).** `emit_strategy_plan` writes one
`PlannedSection` per pack-skeleton section (`load_pack(...).letter_sections`; a missing
skeleton raises `LetterStructureMissing` — never invented). `allowed_tokens`: an
`intro_and_representation` gets `[]`; a section whose `required_token_kinds` include
`fact` gets ALL bare FACT ids, one including `amount` gets ALL bare AMT ids.
`required_tokens` are the fixed source-ref rules — `liability` → the incident FACT (when
minted), `injuries_and_treatment` → the first ≤3 encounter FACTs by ordinal,
`damages_and_specials` → the grand-billed + demand-basis AMTs, `demand_and_deadline` →
the demand-basis AMT. Every id is BARE (no bracket shape in a plan row); the plan emits
`approved=False` (an attorney refines at G2.5) with `demand_type="open"` (the
time-limited-demand seam is D7). Emphasis degrades to `[]` when the provider is offline.

**`DrafterPromptSnapshot` (the judge-symmetry lock, §4).** Three separately-assembled
layers so matter text can never overwrite a rule: `rules_blocks` (the verbatim
tokens-only contract + the section contract with each allowed token's display form) ·
`matter_directives` (the attorney's G1.5 inputs verbatim + the plan's emphasis + the
memo's first 800 chars) · late-bound `final_hard_constraints`
(`HardConstraintInputs.to_entries()`, appended LAST so it binds after everything).
`input_hash` = sha256 over the canonical JSON of `[rules_blocks, matter_directives,
final_entries, plan.version, plan.registry_version]`; the compliance judge rebuilds the
snapshot from the same surfaces and re-hashes — a mismatch is a hard G3 block
(`SnapshotDrift`). `build_snapshot` is exposed pure-of-model so the judge re-derives it.

**Two-concern single retry.** (a) a PARSE retry lives inside `draft_section` (`_draft_body`
retries once on a malformed reply so a bad reply costs one extra metered call, never a run
failure); (b) a CONTENT retry lives in the run (`generate._draft_one_section`: validate →
retry once with the violations appended to the prompt tail → re-validate → surface). The
content retry's `retry_violations` are appended to the prompt tail and do NOT enter the
snapshot (snapshot-neutral) — so the regen (compliance) path reuses the same channel
without moving the hash. `draft_section` creates a fresh row per attempt; the caller
folds the retry onto the first slot and drops the extra, so a section maps to exactly one
`DraftSection` row (a **retry-fold** — one row per section).

`DraftSection` (`body_tokenized` = tokens only; `rendered_preview` + `spans` minted at
render; `registry_version`; `validation` ∈ {`passed`, `retry_pending`, `surfaced_failed`};
`prompt_snapshot` JSON). **SSE frames** (`generate`): a `status` `started`, a `status`
`step`/`memo`, one `section` frame per PASSED section (`{section_id, rendered_preview}` —
never the tokenized body), and on all-passed a `gate_ready` `{gate: "compliance_review"}`;
the typed EARLY refusals (`wrong_gate_state` / `no_approved_plan` / `registry_drift`) and
a per-section `section_validation_failed` are `error` frames; a `budget_exceeded` error
stops the run. A run with any surfaced-failed section does NOT advance (draft stays
`drafting`, a `draft_incomplete` status frame). The **`post_draft` seam**: after a draft
validates, the run calls the injected hook WITHOUT a try/except (the compliance wave
injects its G3 pre-check there — `SnapshotDrift`/`DraftRegistryDrift` escapes are the
route's to convert to an error frame). The **memo** is stored on `DemandDraft.memo` and is
excluded from `letter.docx` v1 (an attorney artifact, never sent to the carrier —
ADR-0007).

v1 `[[CITE_n]]` origins are exactly two, both pre-verified (rules-pack statutes +
attorney-supplied authorities); **no LLM-proposed authority in v1**.

## Change rule

A boundary change requiring a contract update: changing the `DraftSection`
tokens-only contract or the `PlannedSection`/allocator shape (the allowed/required
rules, the stage ids); changing the `DrafterPromptSnapshot` symmetry/hash recipe;
changing the two-concern retry budget, the retry-fold (one-row-per-section) rule, or
the rules-blocks-vs-matter-directives layering; changing the `post_draft` seam or the
memo-excluded-from-letter rule; adding a `[[CITE_n]]` origin; emitting anything other
than the documented `generate` SSE frames (incl. the `section` rendered-preview). A
change to any of these lands with a new ADR (cf.
[ADR-0007](../adr/0007-m5-drafting-decisions.md)). Update this file **and**
[`system_contract.md`](../system_contract.md) §1/3/5/6/11/13 in the same PR.
