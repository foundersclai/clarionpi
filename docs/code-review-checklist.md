# Code Review Checklist

Review the diff, not the description. Write findings one line each (`file:line — issue`), grouped **Blockers** then **Follow-ups**. If a finding needs more than two lines, it's a design discussion — say so and take it to the issue, don't expand the bullet.

## Blockers — must fix before merge

- [ ] Correctness: edge cases (empty, null, concurrent, huge input, unicode) handled or explicitly out of scope.
- [ ] Evidence the change was actually run: test output, screenshot, or repro in the PR. "Should work" is not evidence.
- [ ] Security: input validated at trust boundaries; no secrets in code or logs; no injection paths (SQL, shell, path, template).
- [ ] Errors handled where they occur or propagated deliberately — no swallowed exceptions, no bare catch-and-log-continue.
- [ ] Tests: regression test present for bug fixes; new behavior covered; no test deleted, skipped, or weakened.
- [ ] Scope: no unrelated changes or drive-by refactors mixed into the diff.

## Follow-ups — note, don't block

- [ ] Names and structure make sense to a reader with zero context.
- [ ] Dead code, commented-out blocks, and leftover debug logging removed.
- [ ] Docs touched if behavior, config, or API changed (README, AGENTS.md, ADR).
- [ ] A simpler design exists? Say so in one line; don't demand rewrites for taste.

## Extra scrutiny for AI-authored diffs

- [ ] Unfamiliar APIs and imports actually exist (hallucination check).
- [ ] No over-engineering: abstractions, options, or error handling for cases that cannot occur.
- [ ] Comments state constraints, not narration of the diff ("increment counter") or self-justification ("this is now correct").
