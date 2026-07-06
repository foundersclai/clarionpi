"""Brain-2 — attorney-approved structure into persuasive, tokenized prose (M5 Wave B1).

This package backs `docs/module_contracts/app.engine.brain2.md` (system_contract invariants
1, 3, 5, 6, 11, 13). It turns a G2.5-**approved** :class:`~app.models.orm.StrategyPlan` into a
tokenized :class:`~app.models.orm.DemandDraft` — a strategy memo (Opus) plus, per planned
section, a drafter (Opus) bound by a section contract and late-bound hard constraints — and
validates every section **deterministically** before it renders.

Module layout (each is the sole home of its concern):

* :mod:`app.engine.brain2.plan` — ``emit_strategy_plan``: the deterministic section skeleton
  (from the jurisdiction pack) + the Opus emphasis synthesis, written to a ``StrategyPlan`` row.
* :mod:`app.engine.brain2.constraints` — ``build_hard_constraints`` /
  ``render_final_hard_constraints``: the late-bound address-list + no-volunteer + statutory-term
  hard constraints (inv 6).
* :mod:`app.engine.brain2.drafter` — ``draft_section``: the layered prompt (rules-blocks vs
  matter-directives vs late-bound final constraints — the inv-14 port) + the
  ``DrafterPromptSnapshot`` (the judge-symmetry hash).
* :mod:`app.engine.brain2.validator` — ``validate_section``: the deterministic tokens-only
  validator (inv 13 — code owns mechanical verdicts; a caller owns the single retry).
* :mod:`app.engine.brain2.renderer` — ``render_section``: registry-resolved rendered preview +
  the char-offset ``RenderedSpan`` list (inv 11 — nothing token-shaped on the wire).
* :mod:`app.engine.brain2.memo` — ``generate_memo``: the Opus strategy memo (verbatim inputs).
* :mod:`app.engine.brain2.generate` — ``run_demand_generation``: the ``drafting`` SSE run that
  composes the above, surfaces (never loops on) a twice-failing section, and advances the gate.

Tokens-only discipline (inv 5, 11): a ``DraftSection.body_tokenized`` carries only
``[[FACT_n]]`` / ``[[AMT_n]]`` / ``[[CITE_n]]`` / ``[[EX_n]]`` tokens; the drafter sees display
forms via :func:`app.engine.tokenizer.registry.resolve_for_prompt` and NEVER mints; the
``section`` SSE emits a rendered preview, never the tokenized body.
"""
