# ADR-0008: M6 provenance viewer / anchor-integrity decisions

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** foundersclai

## Context

M6 is the final milestone: the provenance viewer + anchor integrity that makes invariant 2
("provenance or it doesn't ship") verifiable at the attorney's fingertips — click any fact in the
G3 preview / chronology / risk flags and land on its source page. It sits on top of everything M1–M5
already built: ingest is the page-addressable provenance floor (`app/corpus/ingest`), the tokenizer
resolves a token to its `(document, page)` anchors + a verification outcome
(`registry.resolve_for_render`), and the G3 compliance panel already turns a broken anchor into a
hard block (`orphan_token` / `dead_anchor` in `HARD_BLOCK_KINDS`) rather than a render-time surprise.
The E4 provenance report shipped at M5 as one of the four immutable artifacts.

M6 adds the *read* surfaces the viewer needs and the frontend that consumes them:

- **Wave A (backend)** — two routes in `app/api/routes/provenance.py`:
  `GET /api/documents/{id}/blob` (the app-served whole-document PDF bytes) and
  `GET /api/matters/{id}/provenance/{token_id}` (a bare token id → display / outcome / server-enriched
  anchors). Plus the Tier-1 anchor-integrity eval (`tests/evals/test_tier1_anchor_integrity.py` —
  E2 round-trip, E3 dead-anchor detectability).
- **Wave B (frontend)** — a `react-pdf` page viewer with a page-level highlight, `lib/provenance.ts`
  (the sanctioned blob-URL constructor + the lazy `useProvenance` hook), and the `ProvenanceViewer`
  slide-over wired to the compliance-panel spans, chronology rows, and risk flags.

The design source ([`flow_05_provenance_roundtrip.md`](../../backlog/pi/system_flows/flow_05_provenance_roundtrip.md))
sketched a *page* endpoint (`GET .../documents/{doc}/pages/{n}`) returning a **presigned image URL**;
Wave A deliberately shipped a *blob* endpoint serving **app-served bytes** instead. This ADR records
that deviation and the six other M6 decisions that set a boundary or are expensive to reverse. Each
keeps M6 shippable and offline-testable and names the heavier decision it defers (the S1 OCR/coordinates
vendor recurs — it is the counterpart to several deferrals). The M5 drafting/compliance/package
decisions are ADR-0007; the render-span map this viewer consumes persists on `DraftSection.spans`
(M5, ADR-0007 §6 / inv 11).

## Decision

We adopt the following seven decisions for the M6 provenance viewer + anchor-integrity waves.

1. **PDF bytes are APP-SERVED over an authenticated tenant-scoped route, with a `phi_access` audit per
   blob fetch; the token/metadata lookup is NOT audited.** `GET /api/documents/{id}/blob` streams the
   whole document's `application/pdf` bytes through `app/core/storage` (the `local` backend has no
   presign; pdf.js seeks client-side within the served bytes), and writes a `phi_access` audit row —
   `{document_id, filename, surface: "provenance_viewer"}` — BEFORE the bytes leave, committed in the
   GET (the deliberate GET-write mirrors the M5 artifact-download precedent,
   `drafting.get_artifact_download`). Serving a case page IS the audited PHI event (inv 7), so two
   fetches leave two rows. The provenance metadata lookup
   (`GET /api/matters/{id}/provenance/{token_id}`) writes NO audit row — it returns anchor *metadata*,
   not PHI bytes; auditing every hover/click of the viewer would drown the PHI trail in noise and blur
   what "PHI access" means. *Rollback:* if/when the S3/R2 object store lands with its BAA (S4), a
   presign path can replace the app-serve — but only if the presign issuance itself is audited (so the
   `phi_access` row still precedes egress); the route contract (inline PDF, tenant-scoped 404) is
   stable across that swap.

2. **Highlights are PAGE-LEVEL; `bbox` is reserved on the wire but never populated until the S1
   vendor.** Every anchor carries `bbox: null` and the viewer frames the whole cited page (an amber
   ring + a "Cited: page N of M" banner), because the current extraction pipeline emits page anchors,
   not token-precise regions — there are no reliable word-box coordinates to draw before the S1
   OCR/coordinates vendor. The `bbox` key is on the wire shape *now* so that a bbox-emitting extractor
   is a data change (populate the field), not a wire-contract change (add the field). *Rollback:* when
   S1 lands token-precise boxes, populate `bbox` and the viewer draws the box instead of the page frame
   — no route/response-shape change, and the page-level fallback remains for anchors without a box
   (flow_05 §4). The open question of whether page-level fallback meets the attorney's bar for
   high-stakes `[[AMT_n]]` line items (flow_05 §6) is deferred to that vendor call.

3. **The wire accepts BARE token ids only, validated by a strict regex → 422 (tokenize-or-omit at the
   read surface).** `provenance/{token_id}` accepts `^(FACT|AMT|CITE|EX)_\d+$` (e.g. `FACT_7`) and
   422s `invalid_token_id` on anything else — the bracketed `[[FACT_7]]` and lower-case shapes are
   rejected too, so nothing token-shaped is ever accepted *on the path* either (the tokenize-or-omit
   invariant, inv 5/11, applied to the read surface, not just the response). A malformed id is a client
   error (422), distinct from a well-formed-but-unknown id (404 `token_not_found`). *Rollback:* none
   needed — this is the minimal grammar; a new namespace would extend the alternation in one place
   (`_BARE_TOKEN_RE`).

4. **The frontend has ONE sanctioned blob-URL constructor (`blobUrlFor`); token mode consumes the
   server's `blob_url` verbatim.** In anchors mode (pre-resolved anchors, no fetch) the FE builds the
   URL through `lib/provenance.ts::blobUrlFor` — the single place a blob URL is constructed — and in
   token mode it uses the `blob_url` the provenance response already carries, never re-deriving it. The
   backend is the authority on the route shape (`_blob_url` in the route); the FE never hand-assembles
   `/api/documents/.../blob` inline. *Rollback:* if the blob route path ever changes, both the server
   `_blob_url` and the FE `blobUrlFor` move together — the single-constructor discipline keeps that a
   two-line change, not a grep-the-frontend hunt.

5. **`react-pdf@9.2.1` with a same-origin worker emitted by the bundler — not raw `pdfjs-dist`, not a
   CDN worker.** The viewer renders via `react-pdf@9.2.1` (its peer range covers React 19; it bundles
   `pdfjs-dist` 4.8.69), and the pdf.js worker is loaded as a same-origin, fingerprinted static asset
   via `new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url)` at module load in
   `components/pdf-page-view.tsx` — emitted correctly under BOTH webpack `next build` and Turbopack dev,
   with no CDN and no copy-to-`public` step. `react-pdf` owns the canvas lifecycle (mount/unmount, DPR,
   re-render) that a raw `pdfjs-dist` integration would force us to hand-roll; a CDN worker would put a
   PHI-adjacent asset on a cross-origin, non-BAA host. *Rollback:* if `react-pdf` ever blocks a React
   upgrade or a viewer feature, drop to `pdfjs-dist` directly — the worker-as-bundler-asset mechanism is
   already the pdf.js-native path and would carry over unchanged.

6. **Out-of-bounds / superseded anchors are DISPLAY WARNINGS, not fetch blockers (fail-visible,
   attorney judgment).** The provenance response joins each anchor's `page_count` and a
   dedup-`superseded` flag server-side; when `page > page_count` or `superseded: true`, the viewer shows
   a warning but STILL lets the attorney open the document — it never silently hides the anchor or
   refuses the fetch. A broken anchor is already a hard G3 block upstream (inv 2, `dead_anchor`), so by
   the time a letter reaches the viewer these should be rare; surfacing rather than swallowing a
   surviving one is the honest posture (a missing/expired blob is the one hard case → "source document
   unavailable" on a 404, which is a genuine absence, not a judgment call). *Rollback:* none needed —
   fail-visible is strictly safer than hiding; if a class of warning proves pure noise, tighten the
   *upstream* G3 check, not the viewer's willingness to show it.

7. **The `<2s` DoD is measured at the SERVER loop now (~45ms); the full browser cold-render is deferred
   to live pilot hardware.** The M6 exit criterion is `<2s` to a highlighted source page on the
   1,000-page fixture (05 M6). What the fast suite measures deterministically is the SERVER floor: the
   token→provenance→blob HTTP loop, ~45ms with no network and no pdf.js render (printed, informational,
   in the Tier-1 eval and the M6-exit trail). The full cold-render number (presign/serve + pdf.js
   first-paint on a real 1,000-page binder in a browser) is a live-hardware measurement, taken with the
   real viewer at pilot — and it, not the server floor, is what gates the Apryse/commercial-viewer
   fallback decision (03 §8, flow_05 §6). *Rollback:* none needed — the server floor is a true lower
   bound; the browser budget is a separate measurement whose tooling (a headless-browser perf harness on
   pilot hardware) lands with the pilot, not before it has representative data.

## Consequences

- The provenance round-trip is end-to-end runnable and testable offline at M6. The M6-exit E2E
  (`tests/api/test_m6_exit_flow.py`) drives a matter all the way to `package_ready` (reusing the M5-exit
  arc), then over the real HTTP app: for every rendered span it GETs provenance (200, page-level `bbox:
  null` anchors, in-bounds pages), fetches each anchored token's blob (200 `application/pdf`, non-empty),
  and asserts the audit ledger — exactly one `phi_access` row per blob fetch and ZERO audit rows from the
  metadata lookups — plus the negative probes (422 malformed, 404 unknown, 404 cross-tenant). Tier-1
  (`test_tier1_anchor_integrity.py`) proves the same round-trip at 100% at the registry layer (E2) and
  dead-anchor detectability at G3 (E3).
- Each decision names its later counterpart (an audited presign at S4, a bbox-emitting extractor at S1,
  a browser-perf harness + the Apryse decision at pilot) so the deferral is traceable.
- Two invariants advance at the read surface: inv 7 (PHI-access audit logging, previously deferred to
  S1/S4 for the object-store egress path) is now enforced *for the app-served blob* — every byte fetch
  is a committed `phi_access` row before egress; and inv 11's M6 line (the render-span map reaching the
  FE viewer) is realized — the viewer consumes `DraftSection.spans` (bare ids) and the provenance
  response, and the wire-scanner runs on the provenance payload so nothing token-shaped escapes.
- The `bbox`-reserved wire shape means the S1 word-box upgrade is a data + viewer-draw change, never a
  contract renegotiation — the response shape the FE parses is already its final shape.

## Alternatives Considered

- **Presigned page-image URLs (the flow_05 sketch)** — rejected for M6: the `local` storage backend has
  no presign, and a presigned URL is an UNAUDITABLE direct-to-store egress (the byte fetch bypasses the
  app, so no `phi_access` row precedes it) — exactly the inv-7 guarantee M6 exists to make verifiable.
  App-serving the bytes keeps every PHI read on an authenticated, audited, tenant-scoped route.
  *Rollback:* above (1).
- **Word-box (`bbox`) highlights now** — rejected: the pipeline emits no token-precise coordinates
  pre-S1, so a `bbox` would be fabricated; a fabricated highlight box on a legal source page is worse
  than an honest page-level frame. The field is reserved on the wire so the upgrade is data-only.
  *Rollback:* above (2).
- **Accept the bracketed `[[FACT_n]]` token on the path (or a loose id)** — rejected: it would put a
  token-shaped string on the request path, violating tokenize-or-omit at the read surface; the strict
  bare-id regex + 422 keeps the path clean and separates a malformed id (client error) from an unknown
  one (not found). *Rollback:* above (3).
- **Let the frontend hand-assemble blob URLs inline** — rejected: N inline URL constructions drift the
  moment the route path changes; a single `blobUrlFor` (anchors mode) + verbatim server `blob_url` (token
  mode) makes the FE↔route coupling one place. *Rollback:* above (4).
- **Raw `pdfjs-dist` with a hand-rolled canvas, or a CDN-hosted worker** — rejected: raw `pdfjs-dist`
  forces us to own the canvas/render lifecycle `react-pdf` already handles for React 19, and a CDN worker
  puts a PHI-adjacent asset on a cross-origin non-BAA host; the same-origin bundler-emitted worker is
  both simpler and inside the envelope. *Rollback:* above (5).
- **Treat an out-of-bounds / superseded anchor as a hard fetch blocker in the viewer** — rejected: it
  would hide a surviving broken anchor from the attorney (who is the final judgment) and duplicate the G3
  check in the UI; fail-visible (warn, still openable) is the honest posture, with a true 404 blob the
  only hard "unavailable". *Rollback:* above (6).
- **Gate M6 on a measured `<2s` browser cold-render now** — rejected: the fast suite has no
  representative 1,000-page-binder browser hardware, so a synthetic number would be misleading; the
  server floor is measured now and the real cold-render (which gates the Apryse decision) is taken on
  pilot hardware with the real viewer. *Rollback:* above (7).
