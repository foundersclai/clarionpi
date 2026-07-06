# Testing Policy

## What gets tested

- Every bug fix ships with a regression test that fails without the fix.
- Every new behavior ships with tests for the happy path and the most likely failure path.
- Pure logic → unit tests. Boundaries (DB, HTTP, filesystem, queues) → integration tests. Critical user flows → a few end-to-end tests, no more.
- Exempt: throwaway spikes clearly marked as such, and generated code. Nothing else.

## Rules

- Tests are deterministic: no real network, no sleeps for timing, no dependence on wall clock, test order, or shared mutable state.
- The fast suite stays fast (< `<threshold, e.g. 60s>`) so it's actually run after every change.
- A failing test is never deleted, skipped, or weakened to get green. Either the code is wrong (fix it) or the test's expectation is wrong (fix it with human sign-off, saying why in the PR).
- Flaky test → quarantine with a linked issue the same day. Quarantined longer than two weeks → treated as an open bug.

## Coverage

No hard percentage gate. The working rule: changed lines must be exercised by some test, and reviewers check for that instead of a number.

## For agents

- Run the fast suite after each change and the full suite before claiming done; paste the summary line in the PR.
- Fixing a bug: write the regression test first, watch it fail, then fix. A test you never saw fail proves nothing.
