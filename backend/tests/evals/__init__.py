"""Tier-1 extraction-fidelity eval harness (M2 exit criterion + spike S2).

This package holds the *offline-first* Tier-1 harness the M2 exit criterion is proved against:
two synthetic gold matters (:mod:`tests.evals.gold_fixtures`), a pure scorer
(:mod:`tests.evals.tier1`), and the scripted-mode tests (:mod:`tests.evals.test_tier1_extraction`)
that run the whole Phase-0 pipeline against the gold with a deterministic ``ScriptedProvider`` in
the fast suite, plus a single ``@pytest.mark.integration`` live-mode datapoint.

Same gold, same scorer, two providers: the scripted provider proves the harness math + pipeline
plumbing deterministically; the live provider (:class:`~app.core.llm_provider.AnthropicProvider`)
produces the real S2 numbers. The spike CLI over the same pieces is
``backend/scripts/s2_extraction_eval.py`` (results land in ``spikes/s2_extraction_fidelity/``).
"""
