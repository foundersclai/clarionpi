# Definition of Done

A change is done when every line below is true. "Done except…" is not done.

- [ ] **Works:** ran the actual code (not only the tests) and observed the intended behavior.
- [ ] **Tested:** new/changed behavior covered; regression test included for bug fixes; full suite green locally and in CI.
- [ ] **Clean:** lint, format, and typecheck pass with no new warnings.
- [ ] **No debris:** no debug logging, commented-out code, or stray TODOs — convert TODOs to issues.
- [ ] **Documented:** README/AGENTS.md updated if commands, config, or behavior changed; ADR written if architecture changed.
- [ ] **Explained:** PR states what and why, links the issue, and includes evidence of testing (output or screenshot).
- [ ] **Reviewed:** at least one approval; every blocker finding resolved, not deferred.
- [ ] **Reversible:** you can state in one sentence how to roll this back.
