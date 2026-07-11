# Other Findings

Static audit date: 2026-07-06

Reviewed scope: repository structure, Makefile and CI, frontend docs and tests, backend wire
guards, SSE utilities, architecture docs, migrations, and local development guidance.

## OTH-01 - Root verification and CI do not run frontend checks

Priority: High

Finding: Backend verification is wired at the root and in GitHub Actions, but frontend typecheck,
lint, test, and build are not part of `make verify` or the `verify` workflow.

Evidence:

- `Makefile` defines `verify: lint typecheck test hub-check`, all backend/root checks.
- `.github/workflows/verify.yml` installs backend dependencies and runs `make verify`.
- `frontend/package.json` defines frontend scripts, and `frontend/README.md` documents
  `npm run typecheck`, `lint`, `test`, and `build`.

Impact: Frontend regressions can merge even when backend CI is green. This is especially risky
because the workbench is now more than a simple G1/G1.5 shell.

Proposed plan:

1. Add a separate frontend CI job using Node 20 and `npm ci` or the repo-approved install command.
2. Run `npm run typecheck`, `npm run lint`, `npm run test`, and `npm run build`.
3. Either add a root `make verify-frontend` target or document the CI job as the frontend source of
   truth.
4. Cache npm safely in CI while keeping the local temp-cache guidance for this machine.

## OTH-02 - Frontend auth documentation is stale

Priority: Medium

Finding: Frontend comments still say auth endpoints did not exist at the time of the M3-C build,
but the backend now has auth routes.

Evidence:

- `frontend/lib/auth.ts` says `POST /api/auth/login`, `POST /api/auth/logout`, and
  `GET /api/auth/me` did not exist yet at M3-C build time.
- `backend/app/api/routes/auth.py` implements those endpoints.
- `frontend/README.md` still says `lib/auth` degrades until the auth wave lands.

Impact: Stale comments create confusion for future implementation and review work. They can also
hide real auth regressions by making absence sound expected.

Proposed plan:

1. Update frontend auth comments to describe current session/stub behavior.
2. Update `frontend/README.md` to say auth is implemented, with stub/session modes depending on
   backend configuration.
3. Add a small frontend auth test, if missing, that verifies current `me()`/login expectations.

## OTH-03 - Root README under-describes current frontend coverage

Priority: Low

Finding: The root README says the Next.js workbench covers login, matter dashboard, and G1/G1.5
gate screens, but the frontend now includes evidence, plan review, demand generation, compliance,
package, provenance, and other later-stage components.

Evidence:

- `README.md` describes only "login, matter dashboard, G1/G1.5 gate screens".
- `frontend/components/` includes later workflow surfaces such as package, provenance, compliance,
  and plan/demand components.

Impact: The README undersells the implemented MVP and can send reviewers to the wrong mental model
when auditing or onboarding.

Proposed plan:

1. Update the README frontend paragraph to describe the current workbench scope.
2. Keep detailed command and layout guidance in `frontend/README.md` to avoid duplication.
3. Add a brief "current limitations" note that points to the business completeness audit.

## OTH-04 - Token-shaped response scanning is route-local, not centralized

Priority: Medium

Finding: The backend has a wire guard, but response scanning appears to be applied through route or
helper discipline rather than a central JSON/SSE middleware.

Evidence:

- `backend/app/api/wire_guard.py` documents token scanning as a wire-boundary concern and notes
  middleware-style enforcement as deferred.
- SSE helpers and routes rely on code paths using the expected formatting helpers.
- Binary document routes are intentionally outside JSON scanning and need separate artifact checks.

Impact: Route-local enforcement is easy to miss as new endpoints are added. A single unguarded route
could leak internal token-shaped strings into user-visible JSON or event streams.

Proposed plan:

1. Add centralized FastAPI response scanning for JSON responses where feasible.
2. Keep explicit artifact/binary validation for package outputs before storage.
3. Add a single SSE event helper that scans event payloads before serialization.
4. Add tests for JSON routes, SSE routes, and binary/artifact paths so enforcement is hard to
   bypass accidentally.

## OTH-05 - SSE replay and background job durability are deferred

Priority: Medium

Finding: Long-running ingest/drafting flows are exposed through SSE, but replay/resume and durable
background job execution are not yet implemented.

Evidence:

- `backend/app/api/sse_utils.py` documents SSE framing helpers and deferred replay/event-id work.
- Ingest and drafting route docs describe inline or request-owned background runs.
- ADR/docs note later worker/job hardening.

Impact: A browser disconnect or server restart can make users lose progress visibility. Inline
request-owned long work also makes deployment behavior more fragile under proxy timeouts.

Proposed plan:

1. Introduce persisted job rows for ingest, extraction, drafting, compliance, and package builds.
2. Store event history with monotonic IDs and support `Last-Event-ID` replay.
3. Move long work to a worker process or task queue appropriate for the deployment environment.
4. Add frontend reconnect behavior that resumes from the latest known event ID.

## OTH-06 - Build-time generated/data artifacts need a clearer ownership inventory

Priority: Low

Finding: The repo has migrations, contracts, rule packs, package artifacts, and docs with different
ownership expectations. The broad rule "do not hand-edit generated files" exists, but a concise
artifact ownership inventory would reduce review mistakes.

Evidence:

- `AGENTS.md` says not to hand-edit `backend/alembic/versions/*` after they land or other generated
  files.
- `docs/module_contracts/` and `CONTRACTS.md` are drift-checked by `scripts/hub_check.py`.
- Package artifact outputs are generated at runtime and should not become source fixtures unless
  deliberately synthetic.

Impact: Contributors can accidentally patch generated or legally sensitive assets by hand, making
reviews harder and drift more likely.

Proposed plan:

1. Add a short `docs/artifact-ownership.md` table listing generated files, source-of-truth files,
   legal-review-owned files, and runtime artifacts.
2. Link it from `AGENTS.md` and relevant docs.
3. Add hub-check coverage only where the ownership rule can be mechanically enforced.

