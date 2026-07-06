# S1 — OCR vendor bake-off

Executable protocol for spike **S1** (question: **≥98% usable page text + faithful bill
tables at ≤$8/case?**). Binding brief: [`backlog/pi/11_spike_briefs.md` §1–§2](../../../TMEPAgent/backlog/pi/11_spike_briefs.md).
Contestants: **AWS Textract, Google Document AI, Azure Document Intelligence**, with
**Tesseract as the free floor** every paid vendor must beat.

**No live PHI.** Every fixture is synthetic ("Jane Sample") or a founder-produced scan of
a synthetic document. Nothing here originates from a live client matter.

## Tools

Two scripts in `backend/scripts/` (a spike tool tree — not part of `app.*`):

| Script | Role |
|---|---|
| `generate_fc_fixtures.py` | Emits the FC-v1 synthetic corpus + gold labels + `MANIFEST.json`. |
| `s1_scorer.py` | Blind-scores a run directory of vendor transcripts and prints a markdown results table. |

## End-to-end

### 1. Generate the frozen FC-v1 corpus (once, then freeze)

```bash
cd backend
# Small default run (30 EMR pages + 4 bills) — fast smoke:
.venv/bin/python scripts/generate_fc_fixtures.py --out ../spikes/s1_ocr_bakeoff/fixtures --seed 1729
# Full run (matches §1 sizes for the generatable sets):
.venv/bin/python scripts/generate_fc_fixtures.py \
    --out ../spikes/s1_ocr_bakeoff/fixtures --fc1-pages 300 --fcb-docs 80 --seed 1729
```

This writes `fixtures/fc_v1_synthetic/` with:

- `fc1_clean_emr/*.pdf` + `fc1_clean_emr/gold/*.txt` — the **generator is the gold
  labeler** for these synthetic pages (each gold file is exactly the text drawn on the page).
- `fcb_bills/*.pdf` + `fcb_bills/tables/*.csv` — bills plus their gold cell grids (dollar
  cells cent-exact).
- `print_sources/README.txt` + `print_sources/fc3_intake_source_*.pdf` — the physical
  protocol for FC-2 (print → fax/rescan on real hardware) and FC-3 (founder-fill the
  intake forms → scan). Those degraded scans are produced by hand and join FC-v1.
- `MANIFEST.json` — per-file `{set, page_count, sha256, seed}`. **This is the freeze.**
  Additions create **FC-v2**; never re-generate silently over FC-v1.

Same `--seed` + sizes ⇒ byte-identical PDFs ⇒ identical manifest hashes (determinism is
the freeze guarantee).

### 2. Run each vendor OUT OF BAND

Send the FC-v1 page images/PDFs to each OCR vendor **outside this repo** (their own SDK/console).
Collect each vendor's transcripts and any table extractions.

### 3. Assemble a blind run directory

The scorer reads this layout (vendor sub-dirs named **anonymously** — `vendor_a`,
`vendor_b`, … — so grading is vendor-blind; the identity map is kept **outside** the graded
tree and only revealed after scores are recorded):

```
<run-dir>/
  gold/<page_id>.txt         gold transcripts (copy from fixtures' gold/)
  tables/<table_id>.csv      gold cell grids   (copy from fixtures' fcb_bills/tables/)
  vendor_a/<page_id>.txt      vendor A transcript for each gold page
  vendor_a/<table_id>.csv     vendor A cell grid for each gold table
  vendor_b/...                (one dir per vendor)
```

### 4. Score

```bash
cd backend
.venv/bin/python scripts/s1_scorer.py --run-dir <run-dir>
```

Prints the per-vendor markdown table (mean/min page coverage, table F1, counts, pass flags).
**Paste it into `RESULTS.md`** and fill the cost + confidence-calibration columns.

## Scoring rules (from §2, exact)

- **Page-text coverage** = token-level **recall** vs the gold transcript after NFKC +
  casefold + whitespace normalization, counting multiplicity. **FC-1 ≥ 0.98, FC-2 ≥ 0.95.**
- **Table fidelity** = position-aligned **cell F1 ≥ 0.97**, with **dollar cells exact-match
  (a wrong cent fails the cell — no tolerance)**.
- **Cost/latency** = per-page latency and **$/1K pages from actual vendor billing** (not
  list price), in integer cents.
- **Confidence calibration** = do vendor confidence scores predict errors? Feeds the
  low-confidence review-queue design; qualitative, recorded in `RESULTS.md`.

## Blind-scoring rule

**Results are scored before cost is revealed.** No vendor identity is attached to a
transcript during grading (hence the anonymous `vendor_*` dirs) — so an expensive vendor
can't buy a favorable read.

## Decision rule (§2)

**Winner = passes BOTH coverage thresholds at the lowest $/1K pages.** Ties break on
confidence calibration (better error-prediction wins). `s1_scorer.decide()` encodes
lowest-cost-then-lexicographic as its deterministic pick; the calibration tiebreak is a
recorded human call in `RESULTS.md`.

## Kill / rescope triggers (§2, bound to `05 §0`)

- **No vendor reaches ≥95% token recall on FC-2** → MVP intake **narrows to text-layer +
  clean scans**; revisit the degraded-fax path quarterly.
- **FC-B F1 < 90% for every vendor** → **billing extraction goes human-in-loop** (review
  queue) at MVP rather than shipping unreliable auto-extracted specials.

## Governance (§5)

A spike is **DONE only when its decision is written down with the data attached** — a
number without a recorded decision is not a finished spike. Failed thresholds trigger the
named rescope above, never silent acceptance.
