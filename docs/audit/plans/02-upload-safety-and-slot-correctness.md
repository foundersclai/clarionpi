# Upload Safety and Slot Correctness Implementation Plan

Findings covered: `SEC-05`, `BUS-06`

## Goal

Make batch uploads bounded, stream-safe, and impossible to silently pair with the wrong declared
file.

## Current State

- `backend/app/api/routes/uploads.py` reads uploads with `await request.body()`.
- `backend/app/corpus/ingest/sessions.py` stores bytes without checking actual size against the
  declared slot size.
- `backend/app/models/schemas.py` accepts `size_bytes >= 0` with no configured maximum.
- `backend/app/corpus/ingest/sessions.py` documents that slot ordering is not guaranteed to equal
  registration order.
- `frontend/components/documents-panel.tsx` maps returned slots to browser files by array index.

## Non-Goals

- Do not implement S3/MinIO direct-upload storage in this plan.
- Do not change downstream document classification or extraction behavior.
- Do not weaken tenant-scoped 404 behavior for cross-firm upload IDs.

## Implementation Steps

### 1. Add diagnostic logging before fixing the silent mismatch

This is a silent wrong-output risk: bytes can land under the wrong filename with no error. Per the
repo debugging policy, add diagnostics before changing the behavior.

Files:

- `backend/app/api/routes/uploads.py`
- `backend/app/corpus/ingest/sessions.py`
- `frontend/components/documents-panel.tsx`
- `frontend/__tests__/components/documents-panel.test.tsx` (new — the shuffle/mismatch regression
  test asserts on the frontend diagnostic, so it must be a frontend test; step 3 extends this same
  file)
- `backend/tests/corpus/test_uploads_api.py`

Plan:

1. Add non-PHI structured logging under a logger such as `clarionpi.uploads`.
2. Before each frontend PUT, compare the browser filename with the declared slot filename in memory
   and log only the browser file index, slot id, and a `filename_matches` boolean. The test must
   assert that shuffled slots make this boolean false under the current index-based pairing;
   declared-versus-actual byte counts alone cannot prove a swap when two files have the same length.
3. At the backend, log upload session id, slot id, declared byte size, actual byte size, and whether
   they match after the current-body reproduction has been received.
4. Do not log raw filenames, filename hashes, or document content. Filenames can contain PHI and a
   deterministic hash can be dictionary-attacked; the boolean mismatch is sufficient evidence.
5. Add a focused test that simulates shuffled returned slots and confirms the diagnostic mismatch
   before applying the ordinal fix below.
6. Only after that diagnostic test confirms the hypothesis, apply the matching fix below.

### 2. Add a stable slot ordinal to the backend contract

Files:

- `backend/app/models/orm.py`
- `backend/app/models/schemas.py`
- `backend/app/api/view_models.py`
- `backend/app/corpus/ingest/sessions.py`
- `backend/app/api/routes/uploads.py`
- `backend/alembic/versions/<new>_upload_slot_ordinals.py` (hand-written like existing revisions;
  current head is `0009_artifact_sets` — re-resolve `down_revision` at implementation time because
  the auth-hardening and late-document plans also add migrations)
- `backend/tests/models/test_ingest_tables_smoke.py`
- `backend/tests/models/test_upload_slot_ordinal_migration.py` (new focused migration test)
- `backend/tests/corpus/test_upload_sessions.py`
- `backend/tests/corpus/test_uploads_api.py`
- `frontend/lib/types.ts`

Plan:

1. Add `UploadSlot.ordinal: int`, not nullable, with a database unique constraint on
   `(session_id, ordinal)`.
2. Set the ordinal with `enumerate(files)` inside `register_upload_session`.
3. Return `ordinal` in `UploadSlotView`.
4. Order slots by `ordinal` for upload session views and commit document creation.
5. In the migration, add `ordinal` as nullable, backfill every session deterministically by
   `(created_at, id)`, then alter it to non-null and add the unique constraint. This ordering is
   required for populated production databases. Match the old read order so existing sessions keep
   stable behavior (all three read sites order by `(created_at, id)` today: `sessions.py:202-203`,
   `uploads.py:96`, `uploads.py:122`).

Tests:

- Registration returns ordinals `0..n-1`.
- Resume returns slots ordered by ordinal.
- Commit creates documents in ordinal order.
- Existing smoke tests construct `UploadSlot` with ordinal.
- A migration test upgrades a database containing multiple pre-ordinal slots sharing a session and
  verifies their deterministic backfill plus the resulting non-null/unique constraint.

### 3. Update the frontend to match by ordinal

Files:

- `frontend/components/documents-panel.tsx`
- `frontend/__tests__/components/documents-panel.test.tsx` or a new focused test
- `frontend/lib/types.ts`

Plan:

1. Keep the request body as `{filename, size_bytes}` unless a client id is also needed later.
2. Build a `Map<number, File>` from browser file index to file.
3. Upload each returned slot by `slot.ordinal`, not by the returned array index.
4. If a slot ordinal has no matching file, fail the mutation before commit with a clear upload error.
5. Add a test where the mocked registration response returns slots out of order and assert the bytes
   are PUT to the correct upload URLs.

### 4. Enforce upload registration limits

Files:

- `backend/app/core/config.py`
- `backend/app/models/schemas.py`
- `backend/app/corpus/ingest/sessions.py`
- `backend/app/api/routes/uploads.py`
- `backend/tests/core/test_config.py`
- `backend/tests/corpus/test_uploads_api.py`

Plan:

1. Add settings for:
   - `upload_max_files_per_session`
   - `upload_max_bytes_per_file`
   - `upload_max_bytes_per_session`
2. Validate file count and declared sizes during registration.
3. Define and test the exact refusal contract before implementation: all configured registration
   limits return `413` with `{"error": "upload_limit_exceeded", "limit": "max_files" | "max_file_bytes" |
   "max_session_bytes"}`. Catch the service-level exception in the route so these expected client
   errors never become `500`s.
4. Keep Pydantic's static `ge=0` validation, but perform dynamic configured limits in the service
   layer where settings are available. Validate settings are positive and choose bounded defaults;
   validate all declarations before minting slots, storage keys, audit events, or committing.

Tests:

- The three bounded defaults and environment overrides are covered in config tests.
- Zero or negative configured limits are rejected when settings are loaded.
- Too many files are rejected.
- A declared file above max bytes is rejected.
- The aggregate declared session size above max bytes is rejected.
- Valid small uploads continue to work.

### 5. Enforce actual upload byte length without unbounded memory

Files:

- `backend/app/core/storage.py`
- `backend/app/api/routes/uploads.py`
- `backend/app/corpus/ingest/sessions.py`
- `backend/pyproject.toml` (add the Postgres driver needed by the integration test)
- `backend/tests/core/test_storage.py`
- `backend/tests/corpus/test_uploads_api.py`
- `backend/tests/corpus/test_upload_sessions.py` (changing `receive_slot_blob` off its current
  `data: bytes` parameter — `sessions.py:157-176` — breaks its 10 direct
  `receive_slot_blob(..., data=b"...")` call sites in this file; update them to the new
  file-object signature)
- `backend/tests/corpus/test_upload_session_concurrency.py` (new, marked `integration`, for
  Postgres PUT-versus-commit/expiry serialization; do not claim the SQLite unit suite proves row
  locking)

Plan:

1. Extend the storage port with a staged file-object replacement primitive such as
   `stage_fileobj(key: str, fileobj: BinaryIO) -> StagedObjectReplacement`, keeping
   `put(key, bytes)` for existing call sites. The returned handle must expose explicit, idempotent
   `promote()`, `rollback()`, and `finalize()` operations so the ingest service can coordinate the
   filesystem replacement with the database commit without reading a prior blob back into memory.
2. Implement the staged replacement for `LocalDiskStorage` using temporary and backup files under
   the destination's storage-root directory. Staging copies from the current file position without
   changing the live object; promotion atomically installs the new object while retaining enough
   backup state to restore a pre-existing object; rollback restores the prior object (or removes a
   first upload); finalize removes recovery state after database commit. Clean temporary/recovery
   files on every failed operation, and leave a pre-existing object untouched if staging or
   promotion fails.
3. In `put_slot`, stream `request.stream()` into a named temporary file (not an unbounded in-memory
   spool) while counting bytes as chunks arrive, then rewind and pass the file object to
   `receive_slot_blob`, which stages it through the storage port. Preserve tenant-scoped
   slot/session lookup, and reject a non-OPEN session before consuming the request body.
4. Immediately before promoting the staged replacement, have `receive_slot_blob` re-load and lock the
   `UploadSession` row in the current transaction and recheck that it is OPEN. Use the same
   re-load/lock/recheck protocol in `commit_session` and `expire_stale_sessions` before either changes
   session state or deletes blobs: commit must recheck OPEN and completeness under the lock, while
   expiry must recheck both OPEN and `ttl_expires_at < now` after acquiring the lock and skip a row
   whose lifecycle predicate no longer holds. A check against an already-loaded ORM object is stale
   and does not close the PUT-versus-commit/expiry race. Hold the lock through atomic object
   replacement, slot update, and transaction commit. The expiry sweep should skip or safely retry
   rows locked by active uploads.
5. Reject once the count exceeds the configured per-file maximum, stop consuming the request body,
   remove only the staging file, and return `413` with `{"error": "upload_limit_exceeded", "limit":
   "max_file_bytes"}`. Do not invoke storage for this body. If it instead first exceeds the slot's
   declared size, stop streaming and return `422` with `{"error": "upload_size_mismatch"}`.
6. After a complete in-limit stream, reject any remaining actual-size mismatch with `422` and
   `{"error": "upload_size_mismatch"}`.
7. On either rejection, do not change `slot.received`; if a previous valid retry already stored an
   object, preserve that object and received state rather than deleting it.
8. Preserve idempotent retry: a valid re-PUT to an open slot replaces the prior object. Do not claim
   the filesystem replacement and database commit are one atomic transaction. After locking and
   rechecking the session, promote the staged replacement, mark `slot.received`, and commit the
   database; on any pre-commit failure, roll back the ORM transaction and call the replacement
   handle's `rollback()` so both database state and the prior object remain unchanged. Call
   `finalize()` only after the database commit succeeds. Catch request-stream, storage, and database
   exceptions and clean route staging plus storage recovery files before returning or re-raising the
   appropriate failure.

Tests:

- Actual size larger than declared is rejected.
- Actual size smaller than declared is rejected.
- Actual size equal to declared stores and marks received.
- Oversize body stops before storing a complete object.
- Re-PUT of a valid body still works for an open session.
- A non-OPEN session is rejected before its request body is streamed.
- A rejected re-PUT after a successful upload preserves the prior object and `received=True`.
- A forced database commit failure after storage promotion restores the prior object and prior
  `received` state; no staging or recovery file remains.
- Postgres concurrency coverage proves a re-PUT cannot replace a blob after commit or expiry wins
  the session-row lock, and commit/expiry cannot transition or delete midway through a PUT that
  wins the lock. It also proves an expiry candidate observed before waiting is skipped if commit
  changes the session state before expiry acquires the lock. Coordinate the tests with
  events/barriers around the locked section, not sleeps.
- Storage tests cover staged file-object promotion/finalization, rollback after promotion for both
  first-write and replacement cases, replacement atomicity, and cleanup after a copying or promotion
  failure.

## Rollout

1. Land diagnostics and a failing/shuffle regression test first; capture its mismatch evidence
   before changing the pairing behavior, then remove the diagnostic or retain it only at debug level
   after the regression test passes.
2. Land the ordinal schema/API/frontend fix.
3. Land configured registration limits.
4. Land streaming byte enforcement.
5. Update `docs/module_contracts/app.api.view_models.md` and
   `docs/module_contracts/app.corpus.ingest.md` in the same pass — step 2 changes the upload
   view-model contract (`ordinal` in `UploadSlotView`), steps 4–5 add upload refusal contracts, and
   step 5 extends the storage port surface.
6. Update `docs/system_contract.md` in the same pass because the upload response shape and typed
   `413`/`422` refusal vocabulary are public wire-contract changes. Document the three new upload
   limit environment variables in `.env.example` with the chosen bounded defaults.

## Verification

Run:

```bash
rtk test "cd backend && .venv/bin/pytest -q tests/corpus/test_upload_sessions.py tests/corpus/test_uploads_api.py tests/models/test_ingest_tables_smoke.py"
rtk test "cd backend && .venv/bin/pytest -q tests/core/test_storage.py tests/models/test_migration_baseline.py tests/models/test_upload_slot_ordinal_migration.py"
rtk test "cd frontend && npm run test -- documents-panel"
rtk docker compose -f deploy/docker-compose.yml up -d db
rtk proxy sh -lc 'until docker compose -f deploy/docker-compose.yml exec -T db pg_isready -U clarionpi -d clarionpi; do sleep 1; done'
rtk test "cd backend && DATABASE_URL=postgresql+psycopg://clarionpi:clarionpi_dev@localhost:5433/clarionpi .venv/bin/pytest -q -m integration tests/corpus/test_upload_session_concurrency.py"
rtk make verify
```

If frontend CI has not landed yet, also run:

```bash
rtk proxy sh -lc 'cd frontend && npm run typecheck && npm run lint && npm run test && npm run build'
```

## Acceptance Criteria

- No code path relies on returned slot order matching browser file order.
- Uploads are rejected when declared or actual sizes exceed configured limits.
- Declared size and actual size must match before a slot is marked received.
- The API never reads an unbounded upload body into memory.
- Regression tests cover shuffled slot order and byte-size mismatch.
