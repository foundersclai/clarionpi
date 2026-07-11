# app.corpus.ingest

Backs [`system_contract.md`](../system_contract.md) invariants **2, 7, 14**.
Module path: `backend/app/corpus`.
Design source: [`backlog/pi/components/corpus_ingest.md`](../../backlog/pi/components/corpus_ingest.md).

## Status

**Live @ M1 (2026-07-06).** The ingest pipeline is implemented and tested under
`backend/app/corpus/ingest/`:

- `sessions.py` — resumable batch upload (register → PUT-per-slot → commit).
- `classify.py` — Haiku document classification (metered; degrade-to-review).
- `pages.py` — the per-page text pipeline (text-layer fast path → OCR fallback;
  immutable page identity + append-only `PageText` history).
- `dedup.py` — two-stage (exact page-hash + shingle) dedup, quarantined never merged.
- `phase0.py` — the re-entrant `run_phase0` orchestration, streamed over SSE, wired at
  `POST /api/matters/{id}/ingest/run`.

The OCR port is `app/corpus/ocr.py` (`none`/`fake`/`tesseract`); the object-store door is
`app/core/storage.py`. Extraction (`app.corpus.extraction`) remains an M2 stub.

### M1 boundaries

- **Local-disk storage door.** `presign_put` returns `None` on the local backend, so the
  upload target is the slot-addressed `PUT /api/uploads/slots/{id}` (the dev "presign").
  S3/MinIO lands with the prod account (S4/R2).
- **OCR default `none`.** Engine selection is `OCR_ENGINE` (`none`/`fake`/`tesseract`); the
  vendor choice is spike S1. A tesseract adapter is shipped but its binary is absent on the
  bootstrap machine, so image-only pages flag `zero_text` by default rather than pretending
  to OCR.
- **Commit refuses incomplete sessions** (`UploadIncomplete`, naming the missing files) —
  fail-loud, synchronous. There is no `completing` state; commit is not async at M1 (the
  `UploadSessionStatus` omission is deliberate).
- **Slot pairing is by `ordinal` (upload-safety audit, BUS-06).** Every slot carries its
  zero-based registration ordinal (unique per session, DB-constrained); registration,
  resume, and commit all read slots in ordinal order, and the client pairs browser files to
  slots by ordinal — never by response-array index. Commit therefore creates documents in
  exactly the client's declared order.
- **Uploads are bounded and stream-safe (upload-safety audit, SEC-05).** Registration
  enforces `upload_max_files_per_session` / `upload_max_bytes_per_file` /
  `upload_max_bytes_per_session` BEFORE minting any slot/key/audit row (typed
  `UploadLimitExceeded`); the slot PUT streams the body into a spool while counting bytes —
  never `await request.body()` — rejecting over-cap (`413`) and declared-size-mismatch
  (`422`) bodies without invoking storage. Blob swaps go through the storage door's staged
  replacement (`stage_fileobj` → promote/rollback/finalize) bracketing the DB commit, so a
  failed commit restores the prior object and leaves no staging litter.
- **Session lifecycle changes serialize on the session row.** `receive_slot_blob`,
  `commit_session`, and `expire_stale_sessions` each re-load the `UploadSession` under
  `FOR UPDATE` (`populate_existing`) and RE-CHECK their lifecycle predicate under the lock;
  the expiry sweep uses `SKIP LOCKED` (a row held by an active upload is retried next
  sweep) and commits per row. Proven on Postgres by the `integration`-marked concurrency
  suite; SQLite ignores `FOR UPDATE`, so the unit suite does not claim lock semantics.
- **Late-document runs (M4): the `evidence_review` rework edge is live; other states still leave
  the gate untouched.** `run_phase0` processes newly-`uploaded` documents for a matter already past
  `corpus_processing` — extracting them and re-syncing the fact registry + specials ledger. At
  `evidence_review` a late run now **fires the guardless `EVIDENCE_REVIEW -> ANALYSIS_RUNNING`
  rework edge** (`advance` on `DOCUMENTS_UPLOADED`, audited `late_documents_rework`, SSE state
  `late_documents_rework`) so the new facts flow into the demand when the attorney re-runs analysis.
  A late run at ANY OTHER mid-flow state still just processes + re-syncs and **leaves the gate**
  (audited `phase0_late_documents_processed`) — the fuller invalidation of a plan/draft in progress
  is flow_04 work, deferred.
- **TTL sweep is callable-not-scheduled.** `expire_stale_sessions` runs on an unscoped session
  and is invoked directly by callers/tests; there is no scheduler at M1.
- **`doc_state` payload vocabulary** (per the `run_phase0` stream):
  `classifying` `{document_id}` · `classified` `{document_id, doc_type, needs_review}` ·
  `ocr_done` `{document_id, pages_done}` · `failed` `{document_id, reason}` ·
  `dedup_quarantined` `{document_id, dedup_status, against_document_id}` ·
  `extracting` `{document_id}` (framed just before an **extractable-typed** doc enters the
  extractor; a non-extractable type gets no `extracting` frame) ·
  `extracted` `{document_id, rows_emitted, anchors_rejected, runs_failed}` (a doc that reached
  `extracted`) · `extraction_incomplete` `{document_id, runs_failed, error}` (a window failed —
  provider/budget outage or two parse failures — so the doc stays `ocr_done`, resumable). A
  non-extractable doc_type emits **no** extraction frame (logged `doc_extraction_skipped` only).

## Responsibility

Turn raw firm uploads (100s of PDFs, faxed / scanned / clean-EMR) into an
**immutable, page-addressable, provenance-ready store**: every page has a stable
identity, extracted text (text-layer fast path or OCR fallback with per-page
confidence), an object-store image reference, and a dedup verdict. Ingest is the
**sole author of `DocumentPage`** — the anchor target every downstream fact
resolves to. It is **re-entrant**: late document pulls re-enter here, process only
the new documents, and force the matter back through `evidence_review`.

**Not responsible for:** semantic extraction of encounters/bills/incident facts
(`app.corpus.extraction`); PHI *redaction decisions* (flagged here, dispositioned
at G2a / by `app.package.builder`); exhibit selection; any arithmetic or
tokenization. `app/corpus` **never imports `app/engine`** (extraction is strictly
upstream of analysis).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `CaseDocument`, `DocumentPage`, `UploadSession`, `DedupDecision` | — |
| Consumes | upload registration + presigned-PUT requests | app.api.view_models |
| Consumes | OCR results (per-page text + confidence) | external OCR, via the `app/core` BAA inventory |
| Produces | `DocumentPage` rows (text, image ref, confidence) | app.corpus.extraction |
| Produces | `CaseDocument` blobs (`storage_key` → object store) — served **read-only** by the M6 provenance route (`app.api::get_document_blob`); ingest stays the SOLE author of pages/blobs | app.api.view_models → frontend (viewer) |
| Produces | `doc_state` SSE (`{document_id, status, pages_done}`) | app.api.view_models → frontend |
| Produces | dedup + low-confidence review items | frontend (Document Center) |

## Invariants enforced

- **[2]** Ingest **is** the provenance floor: every `DocumentPage` is an
  addressable, **immutable** anchor target (a re-OCR appends a text version and
  moves `active_text_id`; page identity never changes, so `(doc, page)` anchors
  never break). No page → no fact can ship.
- **[7]** OCR runs only against BAA'd endpoints; the object store is inside the
  envelope; `third_party_phi` pages are flagged (not scrubbed here) so they never
  leak.
- **[14]** The ingest phase writes a per-matter run log (documents, page counts,
  OCR fallbacks, dedup verdicts) — silent-corpus debugging starts here.

## Vocabulary

`UploadSession` (resumable presigned PUTs; TTL cleanup) · `DocumentPage`
(immutable identity; `active_text_id` over `text_versions`; `zero_text`) ·
`DedupDecision` (`unique`/`duplicate_of`/`partial_overlap`; page-hash + shingle;
**never silent-merge** — a human resolves `kept` vs `superseded`) · document
lifecycle `uploaded → classified → ocr_done → extracted → failed` (the
`extracted` transition belongs to `app.corpus.extraction`).

## Change rule

A boundary change requiring a contract update: changing the `DocumentPage`
identity/versioning model or the anchor contract; changing the dedup verdict set
or the never-silent-merge rule; changing the `doc_state` SSE shape; changing the
late-document gate behavior (the `evidence_review` rework edge vs the leave-the-gate
default for other states); changing the OCR-vendor egress path.
**`corpus/` importing `engine/` is a boundary breach.** Update this file **and**
[`system_contract.md`](../system_contract.md) §2/7/14 in the same PR.
