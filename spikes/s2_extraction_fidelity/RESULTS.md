# S2 — extraction-fidelity RESULTS

- **Status:** SCRIPTED PLUMBING GREEN · LIVE DATAPOINT PENDING (needs `ANTHROPIC_API_KEY`)
- **Brief:** [`backlog/pi/11_spike_briefs.md` §3](../../../TMEPAgent/backlog/pi/11_spike_briefs.md)
- **Gold:** `backend/tests/evals/gold_fixtures.py` · **Scorer:** `backend/tests/evals/tier1.py`
- **Runner:** `backend/scripts/s2_extraction_eval.py` · **Tests:** `backend/tests/evals/test_tier1_extraction.py`

> **Governance (§5):** this spike is **DONE only when the Decision below is filled with the data
> attached.** A number without a recorded decision is not a finished spike. Failed thresholds
> trigger the named rescope, never silent acceptance.

## Thresholds (§3)

| Metric | Threshold |
|---|---|
| Encounter recall | ≥ 0.95 |
| Encounter precision | ≥ 0.90 |
| Field accuracy (DOS + provider) | ≥ 0.98 |
| Anchor accuracy (exact page, no ±1) | ≥ 0.98 |
| Every encounter/billing line anchored | ratio == 1.0 |
| Billing totals reconcile to gold ledger | cents-exact (delta == 0) |
| Chronology unregistered claims | 0 |
| Merge (GM-1) / dedup exclusion (GM-2) | measured separately (below) |

## Scripted-mode run (CI plumbing proof — deterministic, no model)

The `ScriptedProvider` "perfect-ish" extractor over both gold matters, via `make test` /
`scripts/s2_extraction_eval.py --provider null`. These numbers prove the harness math + pipeline
plumbing (they are NOT the live model's fidelity — see the live table for that):

| matter | prompt_version | provider/model | recall | precision | dos+prov | anchor | ledger exact | unregistered | result | cost (c) |
|---|---|---|---|---|---|---|---|---|---|---|
| gm1 | bill=bill_v1,medical=med_v1,police=pol_v1 | null/claude-sonnet-5 | 1.000 | 1.000 | 1.000 | 1.000 | yes | none | PASS | 8 |
| gm2 | bill=bill_v1,medical=med_v1,police=pol_v1 | null/claude-sonnet-5 | 1.000 | 1.000 | 1.000 | 1.000 | yes | none | PASS | 6 |

- **GM-1 merge:** 6 pull-1 visits (2 recurring in pull-2) + 2 new pull-2 visits = **8 distinct**
  encounters after the deterministic exact-key merge (10 raw rows → 8 survivors; 2 carry a
  reversible `merged_from` snapshot). No LLM tiebreak fires (identical provider/date/type strings).
- **GM-2 dedup exclusion:** the exact byte-copy bill is quarantined `DUPLICATE_OF`, resolved
  `SUPERSEDED`, and its lines never sum — the ledger equals the single-copy total (delta 0¢).

## Live-mode run (the real S2 datapoint — §3 prompt-iteration table, ≤3 rounds)

Fill one row per round from `scripts/s2_extraction_eval.py --provider anthropic` (bump
`PROMPT_VERSIONS` between rounds so the delta is attributable). Cost is the metered `LlmCall` total.

| round | prompt_version | provider/model | recall | precision | dos+prov | anchor | ledger exact | unregistered | cost (c) |
|---|---|---|---|---|---|---|---|---|---|
| 1 (gm1) | _(fill)_ | anthropic/_(model)_ | | | | | | | |
| 1 (gm2) | _(fill)_ | anthropic/_(model)_ | | | | | | | |
| 2 (gm1) | _(bump)_ | anthropic/_(model)_ | | | | | | | |
| 3 (gm1) | _(bump)_ | anthropic/_(model)_ | | | | | | | |

_Each round: bump the changed kind's `PROMPT_VERSIONS`, run, append a row. The row-to-row delta is
the attribution — improvement is data, not vibes._

## Decision

- **M2 exit met (scripted plumbing)?** **YES** — both gold matters PASS every §3 threshold under
  the scripted extractor; the harness math + full Phase-0 → merge → ledger → registry → chronology
  path reconcile (recall 1.000, ledger delta 0¢, anchored ratio 1.000, zero unregistered claims,
  GM-1 merges to 8, GM-2 dup excluded). Evidence attached in the scripted table above.
- **S2 question answered (live Sonnet ≥95% recall + correct anchors)?** **PENDING** — the live
  datapoint requires `ANTHROPIC_API_KEY`; run the live table above and record the result here. The
  scripted pass proves the scorer + pipeline; only the live rows answer the model-fidelity question.
- **Cost datapoint:** scripted mode is nominal (1¢/call). Record the live per-matter `LlmCall`
  total from the CLI output when the live run lands.

### Rescope trigger (§3, bound to `05 §0` — quoted verbatim)

> **<90% recall after round 3** → add a **page-classification pre-pass** and re-scope **M2 (+1
> week)** — matches the 05 S2 kill criterion exactly.

- **Triggered?** _(n — scripted; re-evaluate after the live round-3 row)_

### Governance (§5 — quoted verbatim)

> A spike is **DONE only when its decision is written down with the data attached** — a number
> without a recorded decision is not a finished spike.

## Gold-label provenance (§3 — auditable, not asserted)

- **Labeler:** the gold builders in `backend/tests/evals/gold_fixtures.py` ARE the labeler — every
  encounter/anchor/ledger figure is computed from the same literals rendered onto the fixture pages
  (`_Visit` / `_BillLine` are the single source of truth), so the gold cannot drift from what the
  extractor is handed.
- **Synthetic, no PHI:** all names are obviously fake ("Jane Sample", "Dr. Rivera", "Desert Spine
  PT"); no page originates from a live client matter.
- **Ledger cents:** summed from the printed dollar literals via the money engine's own parser
  (`dollars_str_to_cents`), so the gold ledger and the extractor input share one arithmetic.
