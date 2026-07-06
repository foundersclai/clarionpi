# S2 — extraction fidelity

Executable protocol for spike **S2** (question: **Sonnet structured outputs ≥95% encounter recall
with correct anchors?**). Binding brief: [`backlog/pi/11_spike_briefs.md` §3](../../../TMEPAgent/backlog/pi/11_spike_briefs.md).
S2 is also the **M2 exit criterion**: on two gold fixtures — encounter recall ≥95%, ledger
reconciles to the penny, every encounter/billing line anchored, chronology has zero unregistered
claims.

**No live PHI.** Every fixture is synthetic ("Jane Sample"; providers "Dr. Rivera" / "Desert Spine
PT"). Nothing here originates from a live client matter ([`11 §1`](../../../TMEPAgent/backlog/pi/11_spike_briefs.md)).

## What S2 answers (§3 metrics)

The extractor reads windowed post-OCR page text and emits structured encounters / billing lines /
incident facts with page-level anchors. S2 measures, on the gold set:

- **Encounter recall ≥ 0.95, precision ≥ 0.90** — recall dominates (a missed encounter never
  surfaces; over-extraction is reviewable at G2a).
- **Field accuracy ≥ 0.98 on DOS + provider** — the chronology's spine.
- **Anchor accuracy ≥ 0.98** — a fact's cited page **must contain the fact; exact page, no ±1**.
  (The extractor already drops out-of-window anchors before persistence; the eval verifies the
  extractor cited the RIGHT page per fact, doc-scoped — see `tests/evals/tier1.py`.)
- **Billing totals reconcile EXACTLY** to the gold ledger (cents-exact; feeds the money engine).
- **Merge/dedup measured separately:** GM-1 doubles two visits across two pulls (the exact-key
  merge must collapse them to a distinct count of 8); GM-2 ingests a bill twice (the exact byte-copy
  must quarantine + drop from the ledger).

## Same gold, same scorer, two providers

- **Gold** — two synthetic matters in `backend/tests/evals/gold_fixtures.py`
  (`build_gm1` = the merge matter; `build_gm2` = the exclusion matter). Each carries the fixture
  PDFs, the encounter/ledger truth, and a `scripted_provider_for(...)` factory.
- **Scorer** — the pure `Tier1Report` + `score_matter(...)` in `backend/tests/evals/tier1.py`
  (implements the §3 metrics + thresholds; `Tier1Report.passes()` is the M2 gate).
- **Scripted provider** (fast CI) — a deterministic `ScriptedProvider` plays a "perfect-ish"
  extractor, proving the harness math + pipeline plumbing with no model. Runs in `make test`.
- **Live provider** (the real S2 number) — `AnthropicProvider` against the same gold, in
  `backend/tests/evals/test_tier1_extraction.py::test_live_tier1_passes_m2_exit`
  (`@pytest.mark.integration`, skipped without `ANTHROPIC_API_KEY`).

## How to run

### Scripted (deterministic, no network — the CI plumbing proof)

```bash
cd backend
.venv/bin/pytest tests/evals/test_tier1_extraction.py -m "not integration" -q -s   # prints reports
# or the spike CLI:
.venv/bin/python scripts/s2_extraction_eval.py --matter both --provider null
```

### Live (the real S2 datapoint)

```bash
cd backend
ANTHROPIC_API_KEY=... .venv/bin/pytest -m integration \
    tests/evals/test_tier1_extraction.py -q -s
# or the spike CLI (prints the RESULTS.md row + LlmCall cost):
ANTHROPIC_API_KEY=... .venv/bin/python scripts/s2_extraction_eval.py \
    --matter both --provider anthropic --rounds-note "round 1 — baseline prompts"
```

The CLI builds a THROWAWAY in-memory DB + tmp storage, runs the same flow as the test, prints each
matter's `Tier1Report` as a RESULTS.md markdown row (plus `PROMPT_VERSIONS` and the metered cost),
and exits 0 iff every matter passes. No network unless `--provider anthropic`.

## Prompt-iteration protocol (§3 — ≤3 rounds, attributable)

Improvement must be attributable, not vibes. Each round:

1. **Bump the prompt version** for the kind you changed in
   `backend/app/corpus/extraction/prompts.py` (`PROMPT_VERSIONS`) — the `ExtractionRun` idempotency
   key includes `prompt_version`, so a bump re-extracts every window.
2. **Run** the live CLI with a `--rounds-note` naming the change.
3. **Append a `RESULTS.md` row** for the round (round #, prompt_version, provider/model, recall,
   precision, DOS+provider, anchor, ledger-exact, unregistered, cost). The delta between rows is the
   attribution.

**Rescope trigger (§3, bound to `05 §0`):** **< 90% recall after round 3** → add a
**page-classification pre-pass** and re-scope **M2 (+1 week)**. Write the decision in `RESULTS.md`
(the §5 governance line: a spike is DONE only when its decision is written down with the data
attached).

## S1 dependency note

S2's live input is post-OCR text from the **S1 winner** (S1 decides the OCR vendor). Until S1 picks
a vendor, the gold PDFs here carry a real **text layer** (built via `pdf_builders.build_text_pdf`),
so the extractor reads clean text end-to-end without an OCR step — the extraction-fidelity question
is separable from OCR fidelity. When S1 lands, re-run S2 on the winner's transcripts of the same
gold pages (that is the FC-1/FC-2 window path in §3); the scorer and gold are unchanged.

## Governance (§5)

A spike is **DONE only when its decision is written down with the data attached** — a number
without a recorded decision is not a finished spike. Failed thresholds trigger the named rescope
above, never silent acceptance.
