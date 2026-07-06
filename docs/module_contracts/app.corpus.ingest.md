# app.corpus.ingest

Backs [`system_contract.md`](../system_contract.md) invariants **2, 7, 14**.
Module path: `backend/app/corpus`.
Design source: [`backlog/pi/components/corpus_ingest.md`](../../backlog/pi/components/corpus_ingest.md).

## Status

**Stub @ M0, lands M1.** `app/corpus` is a package stub (empty `__init__.py`).
The `CaseDocument` / `DocumentPage` models and the `DocType`/`DocStatus`/
`DedupStatus`/`TextSource` enums are in `app/models`. No ingest, classify, OCR,
page-store, or dedup logic exists yet.

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
OCR-vendor egress path. **`corpus/` importing `engine/` is a boundary breach.**
Update this file **and** [`system_contract.md`](../system_contract.md) §2/7/14
in the same PR.
