# Component — brain2_drafting

- **Status:** DRAFT for founder review · **Date:** 2026-07-04
- **Planned module path:** `app/engine/brain2`
- **Contract doc (M0):** `docs/module_contracts/app.engine.brain2.md`
- Refines [04 §2 `StrategyPlan`/`DraftSection`](../04_data_model_and_contracts.md),
  [01 §3 Brain-2](../01_high_level_design.md), [01 §8 sequence](../01_high_level_design.md).

## 1. Responsibility

Turn attorney-**approved** structure into persuasive **tokenized** prose. Generate the
strategy memo (Opus) and, per planned section, a drafter (Opus) bound by a section contract
and late-bound hard constraints; validate every section deterministically; regen on failure
or on compliance findings. Output is tokens-only — zero raw provider names, amounts, or
citations reach the wire (invariant 5); rendered previews resolve via the registry (invariant 11).

**NOT responsible for:** *what* to argue (attorney at G1.5/G2.5); arithmetic (references
`[[AMT_n]]` only — invariant 3); rendering/detokenization (`fact_registry` + `package_builder`);
compliance *verdicts* (`compliance_engine`).

## 2. Boundary

| Direction | What | Peer component |
|---|---|---|
| consumes | `StrategyPlan` (section contracts, demand) | orchestrator_gates.md (G2.5 output) |
| consumes | `StrategyInputs` **verbatim** (G1.5 attorney signal) | (via matter state) |
| consumes | `[[FACT_n]]`/`[[AMT_n]]`/`[[CITE_n]]`/`[[EX_n]]` display forms | fact_registry.md |
| consumes | risk dispositions (address list + no-volunteer set) | risk_flag_engine.md |
| consumes | statutory required terms (when active) | jurisdiction_rules.md |
| owns | memo generation, per-section drafter, section validator, `DrafterPromptSnapshot` | — |
| owns | `DraftSection` (tokenized section rows — sole producer; consumed by compliance_engine.md and package_builder.md) | — |
| produces | memo artifact + tokenized `DraftSection[]` | package_builder.md / fact_registry.md |
| produces | `section` SSE (rendered preview, never tokenized) | api_and_wire.md |
| produces | shared prompt snapshot (judge symmetry) | compliance_engine.md |

## 3. Key types & fields

```python
class SectionContract:                     # from the StrategyPlan (04 §2 PlannedSection)
    section_id: str; purpose: str
    allowed_tokens: set[str]; required_tokens: set[str]
    max_words: int

class DraftSection:                        # extends 04 §2 — tokenized, pre-render
    section_id: str; body_tokenized: str   # [[FACT/AMT/CITE/EX]] only (inv. 5)
    registry_version: int                  # resolves at the pinned version
    validation: Literal["passed","retry_pending","surfaced_failed"]

class DrafterPromptSnapshot:               # symmetry lock — judge sees what drafter saw
    section_id: str; input_hash: str
    rules_blocks: list[str]                # rules-lego layer (statutory terms, no-volunteer)
    matter_directives: list[str]           # matter-fact layer (StrategyInputs, dispositions)
    final_hard_constraints: list[str]      # late-bound, appended per section
```

## 4. Internal design

- **Strategy memo (Opus):** inputs = `StrategyPlan` + `StrategyInputs` **verbatim** (attorney
  signal is never paraphrased — TM input-gate-leverage lesson) + registry display forms + risk
  dispositions. The memo frames; it does not decide valuation or emphasis (those are attorney
  judgments already made at G1.5/G2.5).
- **Per-section drafter (Opus) under a `SectionContract`:** `{allowed_tokens, required_tokens,
  max_words}` from the plan. **Late-bound hard constraints** are appended per section: the
  risk-disposition **address list**, the **no-volunteering** rule (invariant 6), and any
  **statutory required terms** from `jurisdiction_rules` when a time-limited demand is active.
- **Layered prompt assembly (port of TM system-contract invariant 14):** `rules_blocks`
  (lego-block rules) are assembled separately from `matter_directives` (matter-specific facts),
  so shared rules can't be silently overwritten by matter text. `final_hard_constraints` bind
  late, per section.
- **Deterministic validator (per section):** all tokens resolve at the pinned
  `registry_version`; `required_tokens` all present; `allowed_tokens` not exceeded; constraints
  hold; length ≤ `max_words`. On fail → **one retry** with the violation appended, then
  **surface** (`surfaced_failed`) — never silently accept (TM structured-output-retries-converge).
- **Tokens-only output contract (invariants 5, 11):** the section body carries only tokens;
  the `section` SSE emits a **rendered** preview resolved via the registry — tokens never touch
  the wire. An unknown token is a validator reject + retry; the drafter **never mints on demand**
  (only `fact_registry` mints — [04 §5](../04_data_model_and_contracts.md)).
- **Compliance regen path:** a `compliance_engine` finding is appended to that section's
  constraints and the section is regenerated (single-section scope, not the whole letter).
- **Snapshot symmetry (`DrafterPromptSnapshot`):** the judge (`compliance_engine`) receives the
  **same** snapshot the drafter saw — `input_hash` locks drafter↔judge symmetry (TM
  DrafterPromptSnapshot analog). **No internal-reasoning SSE events, ever.**

## 5. Invariants enforced

- **3** — no arithmetic here; dollar figures are `[[AMT_n]]` references resolved from the ledger.
- **5** — tokens-only output; raw names/amounts/citations never leave the drafter.
- **6** — address-list constraints honored; no-volunteer set never surfaced.
- **11** — rendered previews via registry resolution; tokens never on the wire.
- **13** — validation is deterministic code; semantic judgment is the LLM judge's, not a
  code-side normalizer patching drafter output.

## 6. Failure modes & handling

| Failure | Detection | Handling |
|---|---|---|
| Drafter emits unknown token | Validator resolution miss | Reject + one retry with violation; never mint on demand; then surface |
| Required token missing / disallowed token used | Validator token-set check | Retry with violation appended; then `surfaced_failed` |
| Oversize section | `> max_words` | Length-bound retry/regen; never truncate silently |
| Constraint violated (volunteered adverse, missing statutory term) | Validator constraint check | Retry with the constraint restated; surface if still failing |
| Model refusal / timeout | Provider error | Retry, then surface run failure — **no silent fallback model** (coupled-decisions lesson) |
| Snapshot hash mismatch drafter vs judge | `input_hash` compare | Block G3; the judge must grade the drafted snapshot, not a drifted one |

## 7. Test strategy

- **Tokens-only output:** a scanner over fixtures asserts no raw provider/amount/citation
  strings in any `DraftSection.body_tokenized`; only registered tokens present.
- **Validator catches planted violations:** unknown token, missing required token, oversize,
  volunteered-adverse — each triggers reject → retry → surface; a clean section passes.
- **Snapshot symmetry:** `judge_input_hash == drafter_input_hash` on fixtures; a mutated
  snapshot fails the symmetry assertion and blocks G3.
- **Verbatim signal:** `StrategyInputs` text appears unaltered in the memo prompt (no
  paraphrase); statutory required terms appear in the constraints when the demand is
  time-limited.

## 8. Decisions (2026-07-04)

Recorded in [10_implementation_readiness.md](../10_implementation_readiness.md) §4:

1. **Retry budget: strict single retry per section**, including length-only failures — the
   "surface, don't loop" discipline holds; a second failure is information for the human,
   not a reason to spin.
2. **The strategy memo is an attorney-visible matter artifact** at G2.5/G3 (stored, shown,
   never sent to the carrier). Hiding the reasoning that shaped the demand would contradict
   the suite's transparency posture; `package_builder` stores it, `api_and_wire` exposes it
   read-only.
3. **`[[CITE_n]]` v1 origins are exactly two, both pre-verified:** (a) `jurisdiction_rules`
   pack statutes/required-language cites (legal-cofounder-verified YAML), and (b)
   attorney-supplied authorities at G1.5/G2.5 (verified-by-attorney on entry). **No
   LLM-proposed authority exists in v1.** The v1.x comparables corpus adds a third source
   behind the same verify-then-mint gate. `allowed_tokens` for authority-bearing sections
   can only ever contain registered CITE tokens.
