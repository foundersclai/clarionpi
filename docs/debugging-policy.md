# Debugging Policy

Core rule: **no fix without a confirmed hypothesis.** A guessed fix that happens to work hides the real bug and teaches nothing.

## The loop

1. **Reproduce.** Get a failing case you can run on demand. If you can't reproduce it, gather evidence — don't fix blind.
2. **Read the actual evidence.** Full error text, stack trace, logs. Don't pattern-match to a familiar failure; confirm this instance matches.
3. **Instrument before fixing.** For silent failures (wrong output, no error in logs), add diagnostic logging at the suspected point *before* changing any logic. Apply the fix only once the log output confirms the hypothesis.
4. **Change one thing at a time.** One hypothesis per attempt. If two variables changed, the result taught you nothing.
5. **Fix the cause, not the symptom.** If the fix doesn't explain every piece of the original evidence, it's a patch over a symptom — keep digging.
6. **Lock it in.** Add the regression test. Remove the diagnostic logging, or downgrade it to debug level only if it earns a permanent place.

## When stuck

After three failed hypotheses: stop. Write down what you ruled out and the evidence for each, then ask for help or file an issue with that writeup. Thrashing destroys the evidence trail for whoever debugs next.

## For agents

- Never claim a bug is fixed without re-running the original reproduction.
- State the hypothesis in the PR or issue before the fix commit, so reviewers can check the fix matches it.
