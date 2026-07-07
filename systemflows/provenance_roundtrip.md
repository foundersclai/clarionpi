# Provenance Round-Trip — Click a Sentence, See the Source, Leave a Trail

The trust feature: any cited span in the letter (and any chronology row or risk
flag) opens the exact source page, highlighted, in a slide-over — and every
byte of PHI served writes an audit row. As built at M6
(`backend/app/api/routes/provenance.py`, `frontend/components/provenance-viewer.tsx`;
decisions in `docs/adr/0008-m6-provenance-decisions.md`).

```mermaid
flowchart TB
    spanClick["Attorney clicks a cited span<br/>in the G3 letter preview<br/>(char-offset spans from the renderer)<br/>or 'View source' on a chronology<br/>row / risk flag"]:::gate
    fe["ProvenanceViewer (token mode)<br/>GET /api/matters/{id}/provenance/FACT_3<br/>bare id on the wire — NEVER token-shaped"]:::auto
    val{"id matches<br/>(FACT|AMT|CITE|EX)_n ?"}:::guard
    r422["422 invalid_token_id"]:::guard
    r404["404 token_not_found<br/>(orphans are not resolvable)"]:::guard
    resp["200: display_form, outcome, source,<br/>anchors: document_id, page, page_count,<br/>superseded, blob_url, bbox = null<br/>(metadata lookup NOT audited)"]:::auto
    blob["GET /api/documents/{id}/blob<br/>app-served inline PDF —<br/>phi_access AUDIT ROW written<br/>BEFORE the bytes leave"]:::registry
    view["PdfPageView<br/>single-page render, amber page ring,<br/>banner: 'Cited: page N of M'"]:::terminal
    warn["superseded or page &gt; page_count<br/>-> display WARNING, never a<br/>fetch blocker (fail-visible)"]:::guard

    spanClick --> fe --> val
    val -- "no" --> r422
    val -- "yes, unknown" --> r404
    val -- "yes" --> resp --> blob --> view
    resp -.-> warn

    classDef auto fill:#eceff1,stroke:#78909c,color:#333
    classDef gate fill:#fff3cd,stroke:#b58900,stroke-width:2px,color:#333
    classDef guard fill:#fdecea,stroke:#c62828,stroke-width:2px,color:#333
    classDef registry fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#333
    classDef terminal fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#333
```

## The audit rule (invariant 7)

- **Byte access is audited; metadata is not.** Fetching a document page writes
  a `phi_access` row per fetch, before the response body. Resolving a token to
  its anchors reveals no PHI and is deliberately unaudited — so the audit log
  measures actual PHI exposure, not UI chatter.
- PDFs are **app-served, never presigned** (ADR-0008 §1 — a presigned URL is
  unauditable egress; the design suite's presigned-image variant was rejected).

## Frontend discipline

- `blobUrlFor` in `frontend/lib/provenance.ts` is the **single sanctioned URL
  constructor**; token mode consumes the server's `blob_url` verbatim. No
  component assembles a document URL by hand.
- Highlights are **page-level only**: `bbox` is reserved in the wire shape and
  stays `null` until the S1 OCR/coordinates vendor lands — the UI never fakes
  precision it doesn't have.
- The pdf.js worker ships as a same-origin bundler asset (no CDN), and blob
  fetches ride the session cookie (`withCredentials`) so the audit row carries
  the real user.

## Integrity backstops (Tier-1, `backend/tests/evals/test_tier1_anchor_integrity.py`)

- **E2:** every minted token's anchors round-trip 100% within page bounds on
  the gold fixture.
- **E3:** a dead anchor (page beyond the document) is *detected at G3* as a
  `dead_anchor` hard block — never silently hidden at render.
- The full token → provenance → blob loop measures ~45 ms server-side; the
  M6 exit test proves audit rows land 1:1 with blob fetches over the real app.
