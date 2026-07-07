# Intake & Phase 0 — from Upload to `corpus_ready`

A paralegal drags in the records corpus; Phase 0 turns it into classified,
deduplicated, extracted pages without losing or inventing anything. The run is
**re-entrant and resumable**: a crashed or interrupted run picks up where each
document stopped (`backend/app/corpus/ingest/phase0.py`).

```mermaid
flowchart TB
    up["Paralegal uploads files<br/>UploadSession + slots (resumable)<br/>commit refuses incomplete sessions"]
    pages["Pages stage — pages.py<br/>pdfplumber text fast path<br/>density floor -> OCR port<br/>(none | fake | tesseract)"]:::auto
    zero["zero_text flag<br/>(OCR_ENGINE=none default)<br/>page kept, flagged for review"]:::warn
    cls["Classify stage — classify.py<br/>Haiku via the metered LLM door<br/>LLM_PROVIDER=null -> doc type 'other'<br/>+ needs_review, run continues"]:::auto
    dd["Dedup stage — dedup.py<br/>page-hash + shingle overlap"]:::auto
    q["PENDING quarantine<br/>human marks kept / superseded<br/>NEVER auto-merged"]:::gate
    ex["Extraction stage<br/>windowed extractors<br/>(see fact_registry_and_money)"]:::auto
    sync["Sync stage<br/>encounter merge -> fact-registry sync<br/>-> specials-ledger AMT emission"]:::auto
    gate["gate step: corpus_ready<br/>matter -> facts_review (G1)"]:::terminal

    up --> pages
    pages -. "no extractable text" .-> zero
    pages --> cls --> dd
    dd -. "suspected duplicate" .-> q
    dd --> ex --> sync --> gate
    q -. "disposed" .-> ex

    classDef auto fill:#eceff1,stroke:#78909c,color:#333
    classDef gate fill:#fff3cd,stroke:#b58900,stroke-width:2px,color:#333
    classDef warn fill:#fdecea,stroke:#c62828,color:#333
    classDef terminal fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#333
```

## Where documents can be, and what the UI sees

- Progress streams over SSE with the closed vocabulary only: `status`,
  `doc_state`, `gate_ready`, `budget_warning`, `error` — there are **no
  internal-reasoning events** by design (`app/models/enums.py::SseEvent`).
- A poison document (corrupt PDF) is marked `failed` and surfaced; it never
  crashes the run or blocks the other documents.
- A document that already finished OCR re-enters at the extraction stage only —
  stages never redo committed work.
- **Late documents after `corpus_ready`** raise the `documents_uploaded` event:
  from `evidence_review` the matter reworks to `analysis_running`; later than
  that, the registry bump cascade applies (see
  [matter_lifecycle](matter_lifecycle.md)).

## Trust properties

- Page identity is immutable: re-OCR **appends** a new `PageText` and moves the
  `active_text_id` pointer — the original extraction is never overwritten.
- Every run writes a per-matter JSON-line log (`app/core/matter_logs.py`), so
  "what happened to this document" is always answerable after the fact.
- Dedup never destroys: suspected duplicates are quarantined for a human
  decision, and the losing copy is marked `superseded`, not deleted.
