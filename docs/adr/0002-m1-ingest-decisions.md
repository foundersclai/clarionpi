# ADR-0002: M1 corpus-ingest implementation decisions

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** foundersclai

## Context

M1 lands corpus ingest — the path from firm uploads to an immutable, page-addressable,
provenance-ready page store (upload sessions → classify → per-page text pipeline → dedup →
the `run_phase0` orchestration streamed over SSE). Several implementation choices along that
path are expensive to reverse (how the job runs, where blobs live, how the text-layer→OCR
handoff decides, how a bad classification behaves, how app-computed timestamps are stored),
so they are recorded here rather than left implicit. Each defers a heavier decision to a
later milestone/spike while keeping M1 shippable, offline-testable, and fail-visible.

## Decision

We will adopt the following five decisions for M1 corpus ingest:

1. **Phase 0 runs inline in the SSE request.** `run_phase0` executes synchronously inside
   `POST /api/matters/{id}/ingest/run` and streams its own frames; no Procrastinate worker is
   wired. There is one consumer, the runner is re-entrant (a re-POST resumes at the first
   unprocessed document), and standing up worker ops before there is a second consumer buys
   nothing. The background-job worker lands with the **M3** orchestrator.
2. **Local-disk object storage behind the `ObjectStorage` door.** Case blobs go to
   `LocalDiskStorage` (traversal-safe relative keys) behind the `app/core/storage.py` port,
   with app-mediated dev uploads (`presign_put` → `None` → the slot-addressed
   `PUT /api/uploads/slots/{id}`). S3/MinIO is deferred to the separated prod account
   (**S4/R2**); swapping it in is a new backend class behind the same door, not a caller change.
3. **Fixed char-density floor for the text-layer→OCR handoff.** A page whose stripped
   text-layer length clears `TEXT_DENSITY_FLOOR` (default 32) wins the fast path; below it the
   page routes to OCR. This resolves the `corpus_ingest §8` open question for M1 with a fixed
   threshold; it is to be revisited against S1's real record sets.
4. **Classifier degrades to the review queue rather than failing the run.** A provider-down,
   budget-exhausted, or unparseable classification writes `doc_type=other` + `needs_review`
   and still advances the document lifecycle (`corpus_ingest A3/A7`). Review is a queue the
   attorney works, not a stall that blocks ingest of the rest of the corpus.
5. **Naive-UTC datetimes for app-computed timestamps.** App-set timestamps store naive UTC
   (`datetime.now(UTC).replace(tzinfo=None)`) so SQLite (tests/offline dev) and Postgres
   (deploy) round-trip identically and naive-to-naive comparisons hold everywhere.

## Consequences

- Ingest is end-to-end runnable and testable offline at M1 (synthetic PDFs + fake OCR +
  scripted provider), with an M1-exit scale test proving 505 pages / provenance intact.
- Each decision names its later, heavier counterpart (M3 worker, S4/R2 prod storage, S1 OCR
  vendor + real record sets) so the deferral is traceable, not silent.
- Inline Phase 0 means one long-lived request per run — acceptable at captive-firm scale for
  M1, but the thing the M3 worker exists to fix.

## Alternatives Considered

- **Wire the Procrastinate worker now** — rejected: no second consumer yet and a re-entrant
  inline runner covers M1; adds ops surface without payoff. *Rollback:* move the `run_phase0`
  body into a Procrastinate task and have the route enqueue + stream job events.
- **S3/MinIO object storage at M1** — rejected: the prod account (and its BAA envelope) is not
  stood up yet. *Rollback:* add an `S3Storage` backend behind `ObjectStorage` and point
  `STORAGE_BACKEND` at it.
- **Learned/adaptive text-density threshold** — rejected: premature without real record sets.
  *Rollback:* replace the fixed floor with a per-record-set calibration once S1 data exists.
- **Fail the run on a bad classification** — rejected: one un-typeable document must not block
  the corpus. *Rollback:* make a degraded classify raise instead of writing `other`+review.
- **Timezone-aware datetimes** — rejected: SQLite stores these naive and would mismatch
  aware-vs-naive comparisons. *Rollback:* move to `TIMESTAMPTZ` + aware datetimes if SQLite is
  dropped from the test matrix.
