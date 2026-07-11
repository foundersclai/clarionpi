# Production Auth Hardening Implementation Plan

Findings covered: `SEC-01`, `SEC-02`, `SEC-03`, `SEC-04`

## Goal

Make production authentication fail closed by default:

- Production cannot boot in stub-auth mode.
- Session cookies are HTTPS-only in production.
- Cookie-authenticated unsafe requests have a CSRF boundary.
- Login attempts are throttled before credential stuffing becomes practical.

## Current State

- `backend/app/core/config.py` defaults `auth_mode` to `"stub"` and reads `AUTH_MODE` with the
  same default.
- `backend/app/api/deps.py` resolves missing-cookie requests to the dev attorney in stub mode.
- `backend/app/api/routes/auth.py` sets the session cookie with `secure=False`.
- `backend/app/api/routes/auth.py` calls `auth_core.authenticate` directly, with no throttle.
- No CSRF middleware or unsafe-method Origin/header check exists in `backend/app/api`.

## Non-Goals

- Do not replace the existing in-house session auth stack.
- Do not add registration, password reset, SSO, or external identity providers.
- Do not remove stub auth from local development or tests.

## Implementation Steps

### 1. Add production settings validation

Files:

- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/tests/core/test_config.py`
- `backend/tests/test_health.py`

Plan:

1. Add strict bool/list parsing helpers and a function such as
   `validate_runtime_settings(settings: Settings) -> None`.
2. Validate `app_env` against the full supported set (`dev`, `test`, `staging`, `prod` — note
   `.env.example:14` currently documents only `dev | staging | prod`; `test` is code-supported at
   `config.py:107` and `tests/api/conftest.py:34`, and `staging` today receives dev-tier defaults,
   e.g. `main.py:40` seeds dev users in any non-`prod` env) and `auth_mode` against
   `stub|session` in every environment, and refuse `settings.app_env == "prod"` unless
   `settings.auth_mode == "session"`. An `APP_ENV=production` typo must not silently select dev
   security defaults.
3. Add `session_cookie_secure: bool` to `Settings`.
4. Default `session_cookie_secure` to `True` when `APP_ENV=prod`, otherwise `False`, unless an
   explicit env var overrides it.
5. Validate once during `app.main` module/app construction, before the `FastAPI` instance is
   exposed, and again from the FastAPI lifespan before `_seed_dev_environment()`. The construction
   check is required for fail-closed production behavior because ASGI lifespan execution can be
   disabled (`uvicorn --lifespan off`); a production process must still refuse invalid auth
   settings even when no lifespan hook runs. The lifespan check keeps startup tests and any
   process that mutates/refreshes settings before startup covered.
6. Keep `get_settings()` side-effect free so tests can construct invalid settings deliberately.

Tests:

- `APP_ENV=prod, AUTH_MODE=stub` raises from `validate_runtime_settings`.
- `APP_ENV=prod, AUTH_MODE=session` passes.
- `APP_ENV=test` and default stub mode still pass.
- `SESSION_COOKIE_SECURE=false` in prod is refused unless the product intentionally adds a separate
  break-glass setting with a test documenting the risk.
- An invalid `AUTH_MODE` is refused instead of silently falling through to stub behavior in
  `get_current_user`.
- An invalid `APP_ENV` is refused instead of bypassing all exact-`prod` guards.
- A `with TestClient(app)` startup test proves the lifespan refuses an invalid production
  configuration before serving `/healthz`; unit-testing only `validate_runtime_settings` is not
  enough to prove the production boot path calls it.
- A subprocess import (or equivalent fresh app-construction test) under
  `APP_ENV=prod, AUTH_MODE=stub` fails before serving even when lifespan is never entered. This
  proves `--lifespan off` or an ASGI server with lifespan disabled cannot bypass the production
  guard.

### 2. Make cookie security environment-derived

Files:

- `backend/app/api/routes/auth.py`
- `backend/tests/api/test_auth_api.py`

Plan:

1. Replace the hard-coded `secure=False` on login with `secure=settings.session_cookie_secure`.
2. Use an explicit shared `path="/"` for set and delete, and pass the configured `secure`,
   `httponly`, and `samesite` posture to `delete_cookie`. Cookie identity is name + domain + path,
   so the shared path is what guarantees deletion; the other attributes keep the response policy
   consistent. Today logout passes only `key` (`backend/app/api/routes/auth.py:132`).
3. Keep `httponly=True` and `samesite="lax"`.

Tests:

- Dev/test login cookie does not require `Secure`.
- Prod settings login cookie includes `Secure`.
- Logout clears the configured cookie name and `path=/`; use an HTTPS `TestClient` base URL for
  the secure-cookie round trip because an HTTP client correctly will not resend a `Secure` cookie.

### 3. Add CSRF boundary for unsafe methods

Files:

- `backend/app/api/csrf.py` or `backend/app/api/middleware.py`
- `backend/app/main.py`
- `backend/app/core/config.py`
- `backend/tests/api/conftest.py`
- `backend/tests/api/test_auth_api.py` (`_probe_client` at :167-182 constructs its own
  `TestClient`; give it the trusted-Origin default too)
- `backend/tests/api/test_csrf.py`
- `frontend/lib/api.ts` (no header changes expected with the Origin approach; just confirm the 403
  `{"error": "csrf_failed"}` body surfaces through the existing `ApiErrorBody` handling)
- `docs/module_contracts/app.api.view_models.md`

Plan:

1. Add `csrf_trusted_origins` to settings. In dev/test, include the Next.js workbench origin
   (`http://localhost:3400`) and the backend origin (`http://localhost:8400`). In prod, require an
   explicit configured HTTPS origin. Parse and validate each value as an origin only: scheme +
   host + optional port, with no credentials, path, query, fragment, wildcard, or `null` origin.
2. Add middleware that runs before route handlers for `POST`, `PUT`, `PATCH`, and `DELETE`.
3. Gate enforcement on an explicit `csrf_enforce` setting: default `True` whenever auth mode is
   `session` (including `APP_ENV=test`) and `False` in stub mode, so tests exercise the same
   session-mode default as production. `validate_runtime_settings` from step 1 refuses
   `csrf_enforce=False` in prod.
4. Update the shared API `TestClient` and any custom session-mode client helpers to send a trusted
   Origin by default. Negative CSRF tests must construct clients/requests without that default or
   override it with an untrusted Origin. Do not disable CSRF for the whole test environment: that
   would let every existing session-mode mutation suite bypass the production control and only
   test an opt-in code path.
5. For session mode, require a single `Origin` header whose serialized origin exactly matches a
   configured trusted origin. Reject missing, duplicate/combined, malformed, `null`, and
   untrusted values. Enforce this on login and logout too; login CSRF can otherwise force a victim
   into an attacker-controlled session.
6. Use the Origin check because the current frontend already sends same-origin browser requests
   through `frontend/next.config.ts`, and this avoids adding a new session-table CSRF token column.
7. Return `403` with `{"error": "csrf_failed"}` on any Origin failure and document this typed
   refusal in the API module contract.
8. Keep non-browser clients possible by documenting how to set a trusted Origin or by adding a
   service-token path in a separate, explicit plan.

Tests:

- Session-mode unsafe request with no Origin is rejected.
- Session-mode unsafe request with untrusted Origin is rejected.
- Session-mode unsafe request with trusted Origin reaches the route.
- Safe methods (`GET`, `HEAD`, `OPTIONS`) are not rejected by CSRF middleware.
- Stub-mode local tests remain green without adding Origin headers everywhere.
- Existing session-mode suites stay green with a trusted Origin supplied by the test harness, and
  at least one existing authenticated mutation test fails with `403 csrf_failed` when that Origin
  is removed. This proves the middleware protects routes beyond its dedicated probe.
- Login with a missing/untrusted Origin is rejected in session mode, while a trusted Origin can
  authenticate successfully.
- Prod settings with `csrf_enforce=False` are refused by `validate_runtime_settings`.
- Prod settings with an empty, HTTP, wildcard, credential-bearing, or path-bearing trusted origin
  are refused.

### 4. Add login throttling

Files:

- `backend/app/core/auth_throttle.py`
- `backend/app/core/auth.py`
- `backend/app/api/deps.py`
- `backend/app/api/routes/auth.py`
- `backend/app/core/config.py`
- `backend/tests/api/test_auth_api.py`
- `backend/tests/core/test_auth.py`
- `backend/tests/core/conftest.py` (shared `make_user` factory at :59-70)
- `docs/module_contracts/app.api.view_models.md` (document the `login_throttled` refusal)
- `backend/tests/models/test_orm_tenancy_shape.py`
- `backend/tests/models/test_migration_baseline.py`
- `backend/app/models/orm.py`
- `backend/alembic/versions/<new>_auth_throttle_buckets.py` (hand-written like existing revisions;
  current head is `0009_artifact_sets` — re-resolve `down_revision` at implementation time because
  the upload-safety and late-document plans also add migrations)
- `docs/adr/<new>-pre-auth-security-tables.md`
- `AGENTS.md` (keep the documented raw backend launch command security-equivalent to `make dev`)
- `Makefile` and every production ASGI launch/process-manager configuration
- `backend/tests/test_launch_config.py`
- `.github/workflows/verify.yml` if the atomic production path needs Postgres-specific coverage

Plan:

1. Add settings for `auth_login_window_seconds`, `auth_login_max_failures_per_account`,
   `auth_login_max_failures_per_ip`, `auth_login_lockout_seconds`, trusted proxy CIDRs, and a
   stable throttle-key HMAC secret. Require a non-placeholder HMAC secret and explicit trusted
   proxy setting in production (the CIDR list may be explicitly empty for direct connections; it
   must not default to trusting arbitrary forwarders); keep deterministic test defaults outside
   production.
   Validate all windows/limits as positive bounded integers and reject internally inconsistent
   values at startup.
2. Make the login identifier unambiguous before building an account throttle. Authentication is
   currently unscoped and calls `one_or_none()` on `User.email.ilike(email)`, but `users.email` has
   no global unique constraint; the same email in two firms can therefore raise
   `MultipleResultsFound`, and an account bucket would not identify one login principal. Because
   the wire login accepts only email (no firm slug), define global canonical email uniqueness:
   add a stored `normalized_email` column populated by one documented trim + casefold function,
   query and throttle by it, and enforce a global unique constraint. Update every production user
   creation path and direct-user test factory/fixture to populate it through the same helper. The
   migration must preflight existing canonical collisions, backfill only when collision-free, and
   fail visibly with remediation instructions rather than choosing a user or deleting data.
   Record the global-login-identifier decision in the auth ADR. If product instead requires the
   same email in multiple firms, change the wire contract to include a firm identifier and key
   throttles by that composite identity; do not ship the ambiguous current lookup.
   Derive `normalized_email` in the ORM itself (a `@validates("email")` hook or equivalent
   construction-time event calling the shared helper): ~19 test modules construct `User` rows
   directly (the shared `make_user` factory at `backend/tests/core/conftest.py:59-70` plus direct
   constructions across `tests/models`, `tests/engine`, and `tests/package`), and a NOT-NULL column
   without ORM-level derivation breaks every one of them; if the hook approach is rejected,
   schedule those modules explicitly instead.
3. Derive the client IP from `request.client.host` by default. Only honor `X-Forwarded-For` when
   the immediate peer belongs to a configured trusted-proxy CIDR; then parse validated IP
   literals right-to-left and select the first untrusted hop. Never trust a header merely because
   it is present or because a boolean says a proxy exists. Reject malformed trusted-proxy chains
   rather than collapsing them into a shared proxy bucket. Configure loopback as trusted for the
   Next dev rewrite, and document the production reverse-proxy CIDRs and header-sanitization
   requirement. Make this application helper the sole owner of forwarded-client resolution:
   Uvicorn 0.50 defaults `proxy_headers=True` and wraps the app in `ProxyHeadersMiddleware`, which
   can rewrite `scope["client"]` before FastAPI sees the request and therefore destroys the
   immediate-peer evidence this trust decision requires. Add `--no-proxy-headers` to `make dev`
   and every production ASGI launch/process-manager configuration (and do not rely on
   `FORWARDED_ALLOW_IPS`) before enabling the application-level parser. Document and verify this
   deployment invariant; otherwise a server-level/app-level double parse can trust or bucket the
   wrong address.
4. Add a DB-backed `AuthThrottleBucket` table with a `scope` (`account` or `ip`), a keyed-HMAC
   digest of the normalized identifier, window/failure/lock timestamps, and a unique constraint on
   `(scope, key_digest)`. Check independent account and IP buckets, returning `429` when either is
   locked. A single pair-only email+IP bucket is bypassable by distributing attempts across IPs
   or usernames and is not sufficient credential-stuffing protection. The HMAC prevents a DB leak
   from turning low-entropy email/IP digests into an offline enumeration list.
   This table is intentionally not firm-scoped because login happens before tenancy is established;
   record that exception in a short ADR, and extend the tenancy-shape invariant test with a
   deliberate, ADR-referenced exemption for this table —
   `backend/tests/models/test_orm_tenancy_shape.py:23-31` asserts every non-`firms` table carries a
   non-null indexed `firm_id`, so without the exemption `make test` fails on the new table.
5. Implement bucket creation/increment/lock as one atomic transaction safe under concurrent
   workers (unique-key insert/upsert plus row lock or equivalent). Lock account/IP keys in a stable
   order to avoid deadlocks, and do not use a read-modify-write sequence that can lose
   simultaneous failures or commit only one of the two bucket updates. Inject/pass the clock so
   window and lockout tests use fixed timestamps with no sleeps or wall-clock dependency.
6. Check both buckets before calling `auth_core.authenticate`
   (`backend/app/api/routes/auth.py:70`).
7. Record failed attempts in both buckets after any failed authentication, including unknown
   emails, without
   revealing whether the user exists. Remove or redesign the current synchronous matched-user-only
   failure audit path in `routes/auth.py`: after `authenticate()` returns `None`, it performs a
   second user lookup and inserts/commits `login_failed` only for a real user, creating
   existence-dependent database work after the dummy password verify. Treat the uniform throttle
   persistence as the failure security record, or add a separate pre-auth audit sink that records
   known and unknown identifiers uniformly; do not retain a firm-scoped-only synchronous write in
   the response path while claiming timing independence. Update the existing API test that expects
   a `login_failed` `AuditEvent` for only the known-user case.
8. After a successful login, clear the account bucket but do not clear the shared IP bucket: one
   valid login must not erase evidence of a password-spraying source. Expire/prune stale buckets so
   the pre-auth table cannot grow without bound.
9. Return `429` with `{"error": "login_throttled"}` plus a correct `Retry-After` header when
   locked out. Keep the body and timing independent of whether the email exists. Document this
   typed refusal in `docs/module_contracts/app.api.view_models.md` alongside step 3's
   `csrf_failed`.

Tests:

- Repeated bad attempts for the same email/IP eventually return `429`.
- Canonical email variants share one login identity and bucket; inserting a cross-firm canonical
  duplicate is rejected, and the migration collision preflight fails without modifying users.
- Attempts for one account across multiple IPs hit the account limit, and attempts from one IP
  across multiple emails hit the IP limit.
- A successful login resets the matching bucket.
- Unknown-email failures are throttled without creating a user-existence oracle.
- The response body for bad credentials remains indistinguishable until the throttle threshold is
  crossed.
- Spoofed `X-Forwarded-For` from an untrusted peer is ignored; a trusted multi-hop proxy chain
  selects the correct client; malformed trusted chains are refused.
- Launch/config regression coverage proves Uvicorn proxy-header rewriting is disabled wherever the
  application parser is used, so tests do not validate a trust model that production bypasses
  before the request reaches FastAPI.
- Known-email and unknown-email failures follow the same synchronous persistence/audit shape after
  password verification; assert the absence of a matched-user-only `AuditEvent`/commit path with
  spies or state assertions rather than a flaky wall-clock timing benchmark.
- A concurrency regression test proves simultaneous failures cannot undercount the threshold. If
  SQLite cannot exercise the production locking primitive faithfully, add a Postgres-marked
  integration test in addition to deterministic unit coverage and add a Postgres service/job to
  `.github/workflows/verify.yml` that runs it. A risky concurrency guarantee cannot depend on a
  documented-but-never-run optional test.
- `alembic upgrade head` creates the new table/constraint and the migration/model drift test covers
  its complete column set; add explicit schema assertions for the canonical-email and bucket-key
  unique constraints because the current drift test compares tables/columns, not indexes.

## Rollout

1. Land config validation and secure-cookie behavior first.
2. Land CSRF middleware gated by the `csrf_enforce` setting from step 3 (on in session mode,
   including tests; off in stub mode; refused off in prod). Configure the production trusted
   origin before deployment.
3. Land the throttle migration and ORM together, run `alembic upgrade head`, and only then deploy
   route code that reads the new table. Configure the stable HMAC secret and trusted reverse-proxy
   CIDRs before enabling the production instance; preserve the HMAC secret across restarts and
   replicas so existing buckets remain effective.
4. Update `.env.example`, `README.md`, and relevant ADR/deploy docs in the same PR set — and pair
   the `docs/module_contracts/app.api.view_models.md` refusal-vocabulary updates (`csrf_failed`,
   `login_throttled`) with the `docs/system_contract.md` §8/11/12/14 update its change rule at
   `app.api.view_models.md:179-180` mandates in the same PR. Update the backend launch command in
   `AGENTS.md` at the same time as `Makefile`; leaving the documented raw Uvicorn command without
   `--no-proxy-headers` would provide a supported-looking path that bypasses the client-IP trust
   invariant.

## Verification

Run:

```bash
rtk make test
rtk make verify
```

Also run targeted tests while iterating:

```bash
rtk test "cd backend && .venv/bin/pytest -q tests/api/test_auth_api.py tests/api/test_csrf.py tests/core/test_config.py"
rtk test "cd backend && .venv/bin/pytest -q tests/models/test_migration_baseline.py tests/models/test_orm_tenancy_shape.py tests/test_health.py tests/test_launch_config.py"
```

## Acceptance Criteria

- Production startup refuses stub authentication.
- Production session cookies include `Secure`.
- Unsafe session-mode requests cannot mutate state without a trusted browser origin or CSRF control.
- Login throttling is covered by tests and documented for deployment.
- Account and IP throttles cannot be bypassed by username/IP fan-out, forwarded-client identity is
  accepted only from configured proxies, and concurrent failures cannot undercount.
- Stub-mode development remains intentionally available outside production.
