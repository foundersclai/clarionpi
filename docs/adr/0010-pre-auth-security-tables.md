# ADR-0010 — Pre-auth security tables, global login identity, and forwarded-client trust

Status: accepted · Date: 2026-07-11 · Source: production-auth-hardening audit
(`docs/audit/plans/01-production-auth-hardening.md`, SEC-04)

> Numbering note: 0009 is deliberately left unallocated — it is reserved for the
> `package_review` gate design named "ADR-0009" throughout
> `backlog/pilot_readiness_refinements_plan.md`, which is on hold until the audit-queue
> controls land.

## Context

Login throttling needs durable failure state **before** any tenant is established, a
single unambiguous login identity to key an account bucket on, and a client-IP trust
decision that reverse proxies cannot spoof. Three decisions fall out of that.

## Decision 1 — `auth_throttle_buckets` is NOT firm-scoped

Every other table carries `firm_id` (the tenancy invariant). Throttle buckets cannot:
login precedes tenancy, and a failed attempt for an unknown email has no firm at all.
The table is the ONE sanctioned exemption, enforced by name in
`tests/models/test_orm_tenancy_shape.py` — any further exemption needs its own ADR.
Bucket keys are keyed-HMAC digests (`AUTH_THROTTLE_HMAC_SECRET`) of the normalized
identifier, so a DB leak cannot be dictionary-attacked back into an email/IP list. The
secret must be non-placeholder in production and preserved across restarts/replicas
(rotating it empties the effective buckets).

## Decision 2 — global canonical login identity (`users.normalized_email`)

The wire login accepts only an email — no firm slug — so canonical email
(`normalize_email` = trim + casefold, one function in `app/models/orm.py`) is the GLOBAL
login identity: `authenticate()` looks up by it, the account throttle keys on it, and a
global unique constraint (`uq_users_normalized_email`) makes the identity unambiguous.
The column derives in the ORM (`@validates("email")`), so every construction path —
production and the many direct test factories — populates it without call-site changes.
Migration 0011 preflights existing canonical collisions and fails visibly with
remediation instructions rather than picking a winner. If the product ever requires the
same email in multiple firms, the wire contract must grow a firm identifier and the
throttle key becomes that composite — the ambiguous `ilike` lookup does not return.

## Decision 3 — the app owns forwarded-client resolution; Uvicorn proxy parsing is OFF

`resolve_client_ip` starts from the immediate peer (`request.client.host`) and honors
`X-Forwarded-For` only when that peer is inside a configured trusted-proxy CIDR
(`AUTH_TRUSTED_PROXY_CIDRS`; loopback by default in dev for the Next rewrite; production
requires an EXPLICIT decision, possibly "no proxies"). Hops parse right-to-left as strict
IP literals; the first untrusted hop is the client; malformed chains from a trusted proxy
are refused (`400 invalid_forwarded_chain`), never collapsed into a shared bucket.

For this to be sound the app must see the REAL peer: Uvicorn defaults
`proxy_headers=True`, whose `ProxyHeadersMiddleware` rewrites `scope["client"]` before
the app runs and would destroy the immediate-peer evidence. Every documented/supported
launch therefore passes `--no-proxy-headers` (never `FORWARDED_ALLOW_IPS`), enforced by
`tests/test_launch_config.py` over the `Makefile` and `AGENTS.md`. Any future production
process-manager config must carry the same flag — that requirement is part of this
decision.

## Consequences

- The failure security record is the uniform throttle row (known and unknown emails take
  the same path); the old matched-user-only `login_failed` audit write was removed — it
  created existence-dependent database work in the response path.
- One valid login clears only the account bucket; the shared IP bucket keeps
  password-spraying evidence. Stale buckets prune opportunistically post-login.
- Bucket writes are row-locked with an insert-retry upsert (stable account→ip lock
  order); the no-undercount guarantee is proven on Postgres by the `integration`-marked
  concurrency test, which CI runs against a real Postgres service.
