"""The G3 compliance panel (lands M5) — deterministic checks + the Sonnet judge + finding lifecycle.

Backs [`system_contract.md`] invariants 2, 3, 6, 11, 13 (see
``docs/module_contracts/app.engine.compliance.md``). Modules:

* :mod:`app.engine.compliance.checks` — deterministic code predicates (orphans, AMT-ledger drift,
  dead anchors, missing exhibits, undisposed adverse, prose-total mismatch).
* :mod:`app.engine.compliance.judge` — the Sonnet semantic judge on the drafter's EXACT snapshot
  (unsupported causation, strategy drift, tone); snapshot symmetry is the load-bearing contract.
* :mod:`app.engine.compliance.corrections` — span-patch (with a runtime fallback to regen),
  single-section regen, and the mandatory re-verify after any fix.
* :mod:`app.engine.compliance.engine` — the pass, bucket routing + severity, the finding
  lifecycle, attorney disposition, and the ``open_blocking_count`` the G3 guard reads.
"""
