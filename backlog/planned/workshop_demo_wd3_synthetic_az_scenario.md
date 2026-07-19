# WD-3 — Owned-synthetic Arizona MVA scenario (thin S12)

- Parent umbrella: workshop **demo** track (WD-0 roadmap); operator-led workshop demo.
- Thins release-track slice: [`workshop_mvp_plan_set_s12_synthetic_scenario.md`](../workshop_mvp_plan_set/workshop_mvp_plan_set_s12_synthetic_scenario.md) (SRC-12).
- Slice ID: WD-3
- Dependencies: none at code level (uses the already-live M1 upload + phase0 ingest path); consumes the demo workbench (M3).
- Mergeability: independent (adds an isolated `workshop/` subtree + `backend/tests/workshop/`; imports nothing in `app/`).
- Deployment: dormant content — inert until an operator uploads it during a demo.
- Safe intermediate state: the scenario is just files; nothing in `app/` imports it, so a partial subtree is harmless.
<!-- sdlc-tier-assessment:start -->
## SDLC tier assessment
- SDLC-Tier: 1
- SDLC-Minimum-Tier: 1
- SDLC-Tier-Status: APPROVED
- SDLC-Tier-Assessor: Claude Opus 4.8 (main session, direct read-only assessment)
- SDLC-Tier-Content-SHA256: fb9ef994579d2bf9e68c5c61f7e4be9e1a95fb798d92b348e81d3acd6598336b
- SDLC-Tier-Base-SHA: 5e45784e21d8e36ca49df3bee0c524cdfa75deb5
- SDLC-Tier-Triggers: none — additive isolated content subtree; no schema/wire/persisted-state/prompt/provider/token/auth/money/concurrency/ownership change; generator is model-free and deterministic
- SDLC-Tier-Approval: user-approved in thread
- SDLC-Tier-Approval-Rationale: recommended — natural Tier 1; only new code is a model-free deterministic PDF generator under an isolated dir imported by nothing in app/, reversible by deletion
- SDLC-Tier-Degraded-Assurance: NONE
- SDLC-Tier-Revalidation: unchanged-tier (BM-03/BM-04 test descriptions synced to as-built pure/offline tests; tier still 1)
<!-- sdlc-tier-assessment:end -->

## Goal and non-goals

- **Goal:** author **one owned-synthetic Arizona private-party MVA case file** — police report,
  medical records, itemized bills (+ one duplicate for dedup) — as reviewable synthetic **source**
  plus a thin deterministic generator that emits **text-layer PDFs** an operator uploads through the
  normal `POST /api/uploads` → phase0 path. This is the demo's input corpus (beats 3–8).
- **Observable success:** the generated PDFs carry a real text layer (`pdfplumber.extract_text`
  returns the authored prose → ingest's fast path, no OCR needed); uploading them classifies into
  `police_report` / `bill` / `medical_record`, surfaces the duplicate to the dedup/review queue, and
  yields a date-of-loss deadline candidate + billed amounts for the downstream gates.
- **Non-goals (this is the THIN slice — the machinery below is release-track S12, not WD-3):**
  no manifest/hash seal, no immutable generation pointer + atomic rename, no crash reconciliation,
  no sealed prohibited-source scanner-as-contract, no version/label identity, no runtime loader, no
  upload-flow mutation, no second scenario, no OCR path, no live records/PHI.
- **Assumptions requiring confirmation:** all names, providers, claim/report numbers, addresses, and
  prose are **newly fictional** and authored fresh — nothing is copied or paraphrased from `samples/`
  (THE RULE), tests, or any real record.

## Live-code grounding

- **New owner (isolated):** top-level `workshop/` subtree — the owned-synthetic demo home the WD-0
  roadmap references. `workshop/README.md` (provenance + owned-synthetic rule),
  `workshop/scenarios/az_mva_01/source/` (reviewable synthetic source, one file per document),
  `workshop/scenarios/az_mva_01/generate.py` (deterministic reportlab text-layer PDF emitter),
  `workshop/scenarios/az_mva_01/README.md` (operator upload script + the fixed "truth" for narration).
- **Reused engine (mirrored, not imported):** the ~15-line reportlab text-layer canvas loop in
  [`backend/tests/corpus/pdf_builders.py`](../../backend/tests/corpus/pdf_builders.py)`::build_text_pdf`
  — WD-3's `generate.py` mirrors it (reportlab imported inside the function, no wall-clock) rather
  than importing across the test boundary (a test module is not a production dependency, and THE RULE
  forbids tests as a content source).
- **Consumers this content feeds (unchanged, already live):** the upload seam
  [`app/api/routes/uploads.py`](../../backend/app/api/routes/uploads.py) →
  `app.corpus.ingest.sessions` (`register_upload_session` → PUT slot → `commit_session`); then phase0
  [`app/corpus/ingest/pages.py`](../../backend/app/corpus/ingest/pages.py) (text-layer fast path),
  [`classify.py`](../../backend/app/corpus/ingest/classify.py) (Haiku over the first-pages text
  sample → closed `DocType`), [`dedup.py`](../../backend/app/corpus/ingest/dedup.py), then the G1
  intake gate + deadline candidates.
- **Forbidden inputs:** `samples/`, `backend/tests/` content, real case records, PHI, attendee docs.
- **Contracts:** none changed. WD-3 adds no schema, wire, state, prompt, route, or ownership seam —
  `make hub-check` is unaffected (no module-contract surface is touched).

## Data flow and blast radius

Reviewable synthetic source (text) → `generate.py` (reportlab, deterministic, model-free) →
text-layer PDFs under `workshop/scenarios/az_mva_01/pdf/` → operator uploads via the normal
`/api/uploads` batch → existing phase0 ingest classifies/dedups/extracts. **Blast radius: nil inside
`app/`** — no production module imports the subtree; the only new code is a standalone generator and
its tests. Reversible by deleting the subtree.

### Classification note (why the content is authored the way it is)

`classify.py` is LLM-based (Haiku via the metered client). Under the **default** `LLM_PROVIDER=null`
every doc degrades to the **review queue** (`needs_review`, `doc_type=other`) by design — which is
itself demo beat 3 (the operator reclassifies). Under `LLM_PROVIDER=anthropic` the first-pages text
sample auto-types each doc. So each source doc's **first page** must carry the discriminating cue
(`_DOC_TYPE_GLOSSES`): the police report headed as an Arizona crash/collision report, bills headed as
itemized statements of charges, records headed as clinical encounter notes. The scenario therefore
demos correctly **both** with a live provider (auto-classify) and with the default (review queue).

### Money discipline (no currency math)

Every dollar figure on every bill — line items **and** totals — and the scenario's grand-billed /
demand-basis "truth" are authored **literals** (fixed synthetic money truth). `generate.py` prints
strings and performs **no** currency arithmetic; `app/money` is not involved and no float-currency
rule is in scope. Any total that must equal a sum of line items is authored to match by hand, not
computed.

## Document set (authored to exercise beats 3–8)

| # | Document | First-page cue → target `DocType` | Carries |
|---|---|---|---|
| 1 | AZ Traffic Collision Report | "Arizona Crash Report" → `police_report` | **date of loss**, synthetic parties, at-fault finding, report # |
| 2 | ER encounter note | "Emergency Department Note" → `medical_record` | admit date, chief complaint, synthetic ICD dx, plan |
| 3 | ER itemized bill | "Itemized Statement of Charges" → `bill` | literal line charges + literal total |
| 4 | Orthopedic clinic notes (4 visits) | "Orthopedic Clinic Progress Note" → `medical_record` | follow-up dates, findings |
| 5 | Orthopedic bill | "Itemized Statement of Charges" → `bill` | literal charges + total |
| 6 | Physical-therapy notes | "Physical Therapy Daily Note" → `medical_record` | visit dates |
| 7 | PT bill | "Itemized Statement of Charges" → `bill` | literal charges + total |
| 8 | **Duplicate of #3** (ER bill re-sent) | same bytes as #3 | exercises **dedup** → review queue |

(A representative subset of S12's "nine PDFs"; sufficient for every demo beat. Extending toward the
full nine — e.g. an imaging report + an `insurance_corr` claim letter carrying the claim # — is
in-scope-optional and additive, no new machinery.)

## Boundary and adversarial test matrix

Tier-scoped to the seams WD-3 actually introduces (the generator + the owned-synthetic guard); the
already-live ingest/classify/dedup path is exercised by its own M1 suite and is **excluded** here
(WD-3 changes none of it). Tests live in `backend/tests/workshop/`.

| ID | Surface | Happy | Negative/Edge | Deterministic test mapping |
|---|---|---|---|---|
| BM-01 | `generate.py` text layer | each emitted PDF's `pdfplumber.extract_text` returns the authored first-page cue + key facts | a doc with no text layer would fail the floor (guards against an image-only regression) | `backend/tests/workshop/test_az_mva_scenario.py::test_every_pdf_has_readable_text_layer_with_expected_cue` |
| BM-02 | `generate.py` determinism | regenerating yields byte-identical PDFs (no wall-clock/random) | — | `backend/tests/workshop/test_az_mva_scenario.py::test_regeneration_is_byte_identical` |
| BM-03 | owned-synthetic guard (THE RULE) | `generate.py` imports resolve to stdlib + reportlab only (AST allowlist); source prose contains none of a denylist of real surnames/entities from `samples/` court records | denylist hit or a non-allowlisted import fails loud | `backend/tests/workshop/test_az_mva_scenario.py::test_source_is_owned_synthetic_no_forbidden_provenance` |
| BM-04 | ingest fast-path + dedup (pure proof of the demo beat) | every generated page clears the real ingest floor via `pages.density_ok(text, text_density_floor)` → phase0 takes the `TEXT_LAYER` fast path, never OCR; the re-sent bill (#8) is byte-identical to #3 → phase0 exact-match dedup fires | — | `backend/tests/workshop/test_az_mva_scenario.py::test_generated_pdfs_clear_the_ingest_text_floor_and_duplicate_dedups` |

<!-- matrix-attestation:start -->
- Reviewer/context:
- Matrix-Completeness-Gate:
- Matrix-Deferred-Findings:
- Matrix-Review-Content-SHA256:
- Matrix-Review-Base-SHA:
- Matrix-Review-Worktree:
- Changed seams and fallback/legacy paths audited:
- Every populated axis → exact deterministic test mapping confirmed:
- Producer failure + consumer response pairs confirmed:
- Forbidden side-effect assertions confirmed:
- N/A axes and concrete reasons confirmed:
- Pre-implementation findings resolved and plan re-reviewed:
- Late-gap rule acknowledged:
<!-- matrix-attestation:end -->

## Red-test evidence before production code

- Commands: `backend/tests/workshop/test_az_mva_scenario.py` (BM-01..BM-04) run before `generate.py`
  / the source exist.
- Expected failures: import/collection error (no `workshop` scenario module yet), then assertion
  failures on missing PDFs / missing text layer.
- Characterization exception: the tests assert **fixed authored synthetic truth** (cues, determinism,
  provenance), not general extraction quality.
- LLM-integration note: `generate.py` is local and model-free, and every test is pure/offline (no
  DB, no provider, no network) — there is no metered spend to assert; the live classify path is
  unchanged by WD-3 and stays covered by the M1 suite.

## Implementation sequence

1. Create `workshop/README.md` — owned-synthetic provenance + rule (nothing from `samples/`, no PHI,
   all fictional), cross-referencing `samples/README.md` THE RULE.
2. Author `workshop/scenarios/az_mva_01/source/*` — the reviewable synthetic text for each document
   in the set above (fixed dates, literal dollar figures, synthetic parties/providers/numbers).
3. Write the failing `backend/tests/workshop/test_az_mva_scenario.py` (BM-01..BM-04); run red.
4. Write `workshop/scenarios/az_mva_01/generate.py` — deterministic reportlab text-layer emitter
   (mirrors `build_text_pdf`; no wall-clock/random; no currency arithmetic). Generate the PDFs.
5. Author `workshop/scenarios/az_mva_01/README.md` — operator upload order + the fixed "truth"
   (date of loss + deadline, grand-billed total, demand basis) for demo narration.
6. Green the suite; `make verify`; self-review.

## Verification and acceptance

- `make verify` passes; new tests green.
- Regenerating the PDFs is byte-identical (BM-02); every PDF has a readable text layer (BM-01).
- The source is demonstrably owned-synthetic (BM-03) and imports nothing from `samples/` or tests.
- Under a scripted provider the scenario ingests and classifies as authored, and the duplicate is
  flagged (BM-04) — the demo input corpus works end-to-end through the already-live phase0 path.
- No `app/` module, schema, wire, contract, or `app/money` surface is touched (`make hub-check` clean).
