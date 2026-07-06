# ADR-0004: M3 Wave A auth (session auth + roles) decisions

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** foundersclai

## Context

M3 Wave A lands real authentication + role guards on top of the M0 dev-attorney stub
(`app/api/deps.py`), which the whole API depends on through a single `get_current_user` door.
The design suite named `fastapi-users` + argon2 + TOTP. Several choices here set a boundary later
milestones (the gate wave, R2 hardening) build on, or are expensive to reverse, so they are
recorded rather than left implicit. Each keeps M3 shippable and offline-testable while naming the
heavier decision it defers.

## Decision

We adopt the following five decisions for M3 Wave A auth.

1. **Lean in-house session auth, not `fastapi-users`.** Argon2 password hashing
   (`app/core/auth.py`), an opaque-token server-side session table (`auth_sessions`), an HttpOnly
   cookie, and a `require_role` guard factory — behind the existing `get_current_user` dependency.
   `fastapi-users` was rejected because its user adapter would force reshaping the already-existing
   `FirmScoped` `User` model, a captive firm needs none of its registration/verification/email
   machinery, and adopting it buys features we do not want at M3. *Rollback:* swap the `auth.py`
   door (and `deps.get_current_user`'s session branch) for a `fastapi-users` backend behind the
   same `get_current_user` dependency — call sites do not change.
2. **TOTP deferred to R2 hardening.** No second factor ships at M3. The open design question is
   restated for R2, not answered here: **mandatory-for-attorney vs per-firm-configurable** MFA, and
   its pilot-onboarding UX. Deferring keeps M3 to a single password factor while leaving the door
   (one `get_current_user` dependency) as the place a second factor later attaches. *Rollback /
   forward:* add a TOTP verify step inside the session-mode branch of `get_current_user` (or a
   post-login step-up endpoint) when R2 chooses the policy.
3. **`AUTH_MODE=stub` retained for dev/test, prod-guarded.** The M0 dev-attorney stub survives
   behind `AUTH_MODE` (`stub` is the dev/test default; `session` is the real path), so every
   pre-M3 test keeps passing unchanged and `make dev` needs no login. The dev-user seed
   (`seed_dev_users`) is refused when `APP_ENV=prod`. In stub mode a *valid* session cookie still
   wins, so the FE can develop real logins against a stub backend. *Rollback:* delete the stub
   branch and make `session` the only mode once the FE + pilot are on real logins.
4. **Opaque server-side sessions, not JWT.** The cookie carries a `secrets.token_urlsafe(32)`
   token; only its sha256 is stored, so a DB leak exposes no usable credential. Chosen over JWT
   because sessions are **revocable** server-side (logout is real), there is **no signing-key
   management**, and the deployment is a **single-box captive** target where a shared session table
   is simplest. *Rollback:* introduce a JWT backend behind the same `get_current_user` door if a
   stateless/multi-box story ever requires it.
5. **Dev seed passwords are non-prod only.** The three seeded dev users (attorney, paralegal,
   admin) share a fixed `dev-password`, gated to non-prod (`seed_dev_users` raises under
   `APP_ENV=prod`) and documented in `.env.example`. They exist so the FE can exercise one login
   per role against a stub/dev backend — never a production credential. *Rollback:* none needed;
   production provisions real users and never runs the seed.

## Consequences

- Auth is end-to-end runnable and testable offline at M3: stub mode keeps the existing suite green;
  session mode is exercised by `tests/api/test_auth_api.py` with `AUTH_MODE=session` set inside the
  test and the settings cache cleared.
- Each decision names its later counterpart (fastapi-users swap, R2 TOTP, JWT backend) so the
  deferral is traceable, not silent; all sit behind the one `get_current_user` door.
- `require_role` returns a **typed 403** (`role_forbidden` + `required` + `actual`) so the FE renders
  the authorization reason inline rather than graying the control out (invariant 8).
- A DB leak of `auth_sessions` yields sha256 hashes only; `users.password_hash` is argon2. Neither
  the raw session token nor a plaintext password is ever stored.

## Alternatives Considered

- **Adopt `fastapi-users` now** — rejected: its adapter reshapes the `FirmScoped` `User`, and its
  registration/verification machinery is dead weight for a captive firm. *Rollback:* above (1).
- **Ship TOTP at M3** — rejected: the mandatory-vs-per-firm policy is unresolved and belongs with
  R2 pilot onboarding; shipping it now would hard-code a policy we have not chosen. *Forward:*
  above (2).
- **Drop the stub, make `session` the only mode** — rejected for M3: it would break every pre-M3
  test's implicit dev-attorney and force a login into `make dev` before the FE is ready.
  *Rollback:* above (3).
- **JWT sessions** — rejected: not revocable without a denylist (which re-introduces server state),
  and signing-key management is overhead a single-box captive deployment does not need. *Rollback:*
  above (4).
