# Security Findings

Static audit date: 2026-07-06

Reviewed scope: backend API dependencies, auth routes, tenancy helpers, upload flow, storage,
LLM provider boundary, OCR boundary, provenance/blob routes, package artifacts, frontend API/auth
clients, CI, repo docs, and module contracts.

## SEC-01 - Production can boot with development stub authentication

Priority: Critical

Finding: `AUTH_MODE` defaults to `stub`, and `get_current_user` intentionally returns the seeded
development attorney when no valid session is present in stub mode. The production seed guard
prevents dev users from being created in prod, but the process can still be misconfigured into a
mode where authentication is not session-enforced.

Evidence:

- `backend/app/core/config.py` sets `auth_mode: str = "stub"` and reads
  `AUTH_MODE` with a default of `"stub"`.
- `backend/app/api/deps.py` dispatches auth by `settings.auth_mode`; session mode rejects missing
  cookies, while stub mode falls back to the dev attorney path.
- `backend/app/api/deps.py` does guard `seed_dev_users` in `APP_ENV=prod`, which is good, but this
  is not the same as a boot-time auth-mode guard.

Impact: A deploy configuration mistake can expose the API without real login enforcement.

Proposed plan:

1. Add a startup/config validation that raises immediately when `APP_ENV=prod` and
   `AUTH_MODE != "session"`.
2. Add a focused config test for prod/stub refusal and prod/session acceptance.
3. Add deployment documentation that marks `APP_ENV=prod`, `AUTH_MODE=session`, and a non-default
   session secret/cookie posture as required production settings.
4. Consider changing the default to `session` outside explicit local/test helpers, with tests
   opting into `stub`.

## SEC-02 - Session cookies are explicitly non-secure

Priority: High

Finding: Login sets the session cookie with `secure=False` unconditionally.

Evidence:

- `backend/app/api/routes/auth.py` calls `resp.set_cookie(..., httponly=True, samesite="lax",
  secure=False)`.

Impact: In a real HTTPS deployment, the browser is not instructed to restrict the session cookie to
HTTPS transport. This increases exposure if a user ever reaches the app over HTTP or a proxy is
misconfigured.

Proposed plan:

1. Add a `session_cookie_secure` setting that defaults to `True` in prod and `False` in dev/test.
2. Use the same setting for login and cookie clearing behavior.
3. Add API tests for cookie attributes in dev and prod settings.
4. Document the reverse-proxy requirement that the app is served only over HTTPS in production.

## SEC-03 - Cookie-authenticated unsafe methods have no CSRF control

Priority: High

Finding: The frontend uses same-origin cookie authentication (`credentials: include` behavior via
the shared API client/proxy), and backend mutating routes rely on the session cookie alone. No CSRF
token, unsafe-method origin check, or central CSRF middleware was found.

Evidence:

- `backend/app/api/routes/auth.py` sets a `SameSite=Lax` cookie.
- `frontend/next.config.ts` and `frontend/lib/api.ts` document same-origin API requests.
- Searching `backend/` and `frontend/` for CSRF-specific controls only found the cookie SameSite
  attribute, not a token or origin validation implementation.

Impact: SameSite=Lax is helpful but not a full CSRF strategy for all browser and deployment edge
cases, especially as more mutating actions are added.

Proposed plan:

1. Choose one CSRF strategy and apply it centrally: synchronizer token/double-submit token, or
   strict Origin/Referer validation for unsafe methods.
2. Expose the token through bootstrap or `/api/auth/me` and have the frontend attach it to
   `POST`, `PUT`, `PATCH`, and `DELETE`.
3. Add tests showing unsafe methods reject missing or mismatched CSRF controls and accept valid
   same-origin requests.
4. Keep `SameSite=Lax` as a defense-in-depth layer, not the only control.

## SEC-04 - Login has no throttling or lockout

Priority: High

Finding: The login route authenticates and returns `401` on failure, but there is no rate limiting,
backoff, account lockout, or IP/email throttle.

Evidence:

- `backend/app/api/routes/auth.py` calls `auth_core.authenticate` directly and audits only failures
  where the email matches an existing user.
- Searches for rate limiting, throttling, lockout, or backoff did not find an implementation in the
  auth path.

Impact: The login endpoint is vulnerable to credential stuffing and online password guessing. The
current audit behavior also leaves anonymous misses without any security event trail.

Proposed plan:

1. Add a small auth throttle keyed by normalized email plus client IP or deployment-provided
   forwarded IP.
2. Use progressive delay or temporary lockouts with user-safe error messages.
3. Audit all failures in a privacy-preserving way, including unknown-email attempts without
   leaking whether the account exists.
4. Add API tests for repeated failures, reset on success, and lockout expiry.

## SEC-05 - Upload handling reads the full request body and does not enforce declared size

Priority: High

Finding: Slot upload reads the entire body into memory and the ingestion layer explicitly treats the
declared file size as advisory.

Evidence:

- `backend/app/api/routes/uploads.py` uses `data = await request.body()` before storing the blob.
- `backend/app/corpus/ingest/sessions.py` states: "Declared size is advisory at M1: a mismatch
  against len(data) is not enforced here."
- `backend/app/models/schemas.py` accepts `size_bytes: int = Field(ge=0)` without an upper bound.

Impact: A large upload can create avoidable memory pressure or storage abuse. Size mismatch also
breaks the trust boundary between registration metadata and uploaded bytes.

Proposed plan:

1. Add configured maximums for files per session, bytes per file, and bytes per session.
2. Reject registration requests that exceed those limits.
3. Stream upload bodies with an enforced byte limit instead of reading the whole request body.
4. Reject uploads where actual byte length differs from the declared slot size.
5. Add API tests for oversize registration, oversize body, and declared-size mismatch.

## SEC-06 - Production object storage backend is not implemented

Priority: Medium

Finding: The storage boundary is clean, but only local disk storage is wired. Any backend other than
`local` raises `StorageNotConfigured`.

Evidence:

- `backend/app/core/storage.py` states only `local` exists and raises for S3/MinIO.
- `backend/app/core/config.py` defaults `storage_backend` to `local`.

Impact: Local disk is acceptable for development, but not enough for a production PHI system that
needs durable object storage, encryption posture, backup/retention controls, and operational
separation.

Proposed plan:

1. Implement an S3-compatible backend with restricted bucket prefixes, server-side encryption, and
   private access.
2. Keep app-mediated reads for PHI audit events, or audit every presigned URL issuance with expiry
   and purpose.
3. Add object-key safety tests to prevent tenant/path escape.
4. Add a deployment check that rejects `STORAGE_BACKEND=local` in production unless an explicit
   one-off local mode is approved.

## SEC-07 - PHI vendor egress governance is incomplete

Priority: Medium

Finding: The LLM provider boundary is centralized and metered, but direct Anthropic API use is
available through `LLM_PROVIDER=anthropic`, and the system contract still records BAA/vendor
inventory work as deferred.

Evidence:

- `backend/app/core/llm_provider.py` implements `AnthropicProvider` and posts prompts to
  `https://api.anthropic.com/v1/messages`.
- `docs/system_contract.md` describes the PHI/BAA envelope and notes deferred vendor inventory and
  enforcement items.

Impact: A production legal/medical workflow needs a clear approved-vendor list before PHI can leave
the system. Central metering is necessary but not sufficient for compliance governance.

Proposed plan:

1. Create a production PHI egress inventory covering LLM, OCR, email, analytics, logging,
   monitoring, storage, and support tooling.
2. Add a runtime guard that allows live LLM/OCR providers only when the vendor is configured as
   approved for the current environment.
3. Document BAA status, data retention, training-use guarantees, and incident escalation paths for
   each vendor.
4. Add a test proving production refuses unapproved live providers.

