# Frontend CI Coverage Implementation Plan

Findings covered: `OTH-01`

## Goal

Make frontend regressions fail CI with the same reliability as backend regressions.

## Current State

- Root `Makefile` verifies backend lint, format, typecheck, tests, and `hub-check`.
- `.github/workflows/verify.yml` installs backend dependencies and runs `make verify`.
- `frontend/package.json` defines `typecheck`, `lint`, `test`, and `build`.
- `frontend/package-lock.json` exists, so CI can use `npm ci`.
- Local install guidance still recommends `npm install --cache "$(mktemp -d)"` because this
  machine's shared npm cache can wedge.
- The frontend suite is not CI-ready at the Node 20 floor this plan targets: collecting
  `__tests__/components/compliance-panel.test.tsx` imports the real chain `compliance-panel.tsx`
  → `provenance-viewer.tsx` → `pdf-page-view` → `react-pdf` 9.2.1 → `pdfjs-dist` 4.8.69, which
  uses `Promise.withResolvers` (added in Node 22) — so under Node 20 collection fails with
  `Promise.withResolvers is not a function`, while on this machine's Node 26 the full suite passes
  (19 files / 143 tests). The dedicated `pdf-page-view.test.tsx` already mocks `react-pdf`, and
  `provenance-viewer.test.tsx` mocks `@/components/pdf-page-view`.

## Non-Goals

- Do not merge backend and frontend dependency installation into one environment.
- Do not replace npm with pnpm/yarn.
- Do not require the backend to be running for unit tests unless a frontend test explicitly needs
  live integration.

## Implementation Steps

### 1. Add frontend verification target

Files:

- `Makefile`
- `frontend/README.md`
- `AGENTS.md`

Plan:

1. Split the current backend checks into `lint-backend`, `typecheck-backend`, `test-backend`, and
   `verify-backend` or keep the existing backend targets and add `verify-frontend` alongside them.
2. Add `verify-frontend` that runs:

   ```bash
   cd frontend && npm run typecheck
   cd frontend && npm run lint
   cd frontend && npm run test
   cd frontend && npm run build
   ```

3. Make root `verify` depend on both backend verification and `verify-frontend`, so the repository's
   single done command covers the full workbench. This requires a backend-only aggregate target
   (e.g. `verify-backend: lint typecheck test hub-check`) because the existing CI backend job must
   switch to it (see step 2) — that job installs no frontend dependencies.
4. Add every new aggregate/command target to `.PHONY`, including `verify-backend` and
   `verify-frontend`, so a same-named file cannot silently suppress verification.
5. Do not have the Makefile run `npm install`; dependency installation belongs to local setup or CI.
6. Update the `AGENTS.md` command table and `frontend/README.md` so they state that root
   `make verify` is the full backend-plus-frontend gate and requires frontend dependencies to be
   installed first. Keep `make test` documented as the fast backend suite; do not imply that it
   covers frontend tests.

### 2. Add a separate GitHub Actions frontend job

Files:

- `.github/workflows/verify.yml`

Plan:

1. Keep the existing backend job, but change its final step from `make verify` to the backend-only
   aggregate (`make verify-backend`): the job installs only Python deps (`verify.yml:18-23`) and
   `frontend/node_modules` is git-ignored, so a root `verify` that includes the frontend would fail
   there — and installing frontend deps into that job would violate the Non-Goals.
2. Add a separate `frontend` job using `actions/setup-node@v4` with Node 20 (matches the existing
   engines floor `"node": ">=20"` in `frontend/package.json`). Treat Node 20 as the compatibility
   floor to verify, rather than raising the CI runtime to hide a jsdom-only import failure.
3. After `npm ci`, run the Makefile target rather than duplicating its script sequence in the
   workflow, so local and CI verification cannot drift:

   ```bash
   cd frontend
   npm ci
   cd ..
   make verify-frontend
   ```

   The target must execute `npm run typecheck`, `npm run lint`, `npm run test`, and
   `npm run build` in that order.

4. Configure `actions/setup-node` with `cache: npm` and
   `cache-dependency-path: frontend/package-lock.json`. There is no root lockfile, so omitting the
   dependency path can make cache setup search the wrong location and fail before verification.
5. Keep the job separate so backend failures and frontend failures are clear in CI.
6. Preserve the existing backend job/check identifier while adding the frontend job. If repository
   branch protection or a ruleset names required status checks individually, add the new frontend
   check as required in the same rollout; otherwise a red frontend job could still be mergeable
   while only the pre-existing backend check is required.

### 3. Keep local cache guidance intact

Files:

- `frontend/README.md`
- `AGENTS.md`

Plan:

1. Keep local install guidance as:

   ```bash
   cd frontend && npm install --cache "$(mktemp -d)"
   ```

2. Explain that CI uses `npm ci` because it runs in a clean environment with a lockfile.
3. Avoid adding a local command that writes to the shared global npm cache on this machine.

### 4. Verify scripts are CI-safe

File to edit:

- `frontend/__tests__/components/compliance-panel.test.tsx`

Files to inspect (edit only when the checks below require it):

- `frontend/package.json`
- `frontend/next.config.ts`

Plan:

1. Fix the existing test-collection failure before making the frontend job required. In
   `frontend/__tests__/components/compliance-panel.test.tsx`, mock
   `@/components/pdf-page-view` using the same boundary as
   `frontend/__tests__/components/provenance-viewer.test.tsx`. The compliance-panel tests verify
   span-to-provenance wiring, not pdf.js rendering; keep real `PdfPageView` behavior covered by the
   dedicated test that already mocks `react-pdf`. Do not add a production `Promise.withResolvers`
   polyfill or weaken/remove the failing suite to make CI green.
2. Run every frontend script under Node 20 after installing dependencies. The test command must
   collect and pass `compliance-panel.test.tsx`, not merely report the other suites green.
3. If `npm run lint` fails because `next lint` behavior changed in Next 15, update the lint script
   deliberately rather than weakening lint coverage.
4. `npm run build` needs no env vars today — `frontend/next.config.ts` reads no `process.env` (the
   rewrite destination is hardcoded to `http://127.0.0.1:8400`). If that changes, make defaults
   explicit in frontend config or document required CI env.
5. Keep tests synthetic and local; do not depend on a live FastAPI backend in CI.

## Rollout

1. Add the Makefile targets, the backend CI job's switch to `make verify-backend`, and the new
   frontend CI job in one PR, together with the test-isolation fix required for the job to pass.
2. Run the new frontend job's command sequence under Node 20 before pushing.
3. Let the first CI run prove lockfile and Node setup.
4. If frontend scripts already fail, fix those failures in the same PR rather than skipping the job.
5. Verify the protected-branch rules after the first run exposes the new check name, and require the
   frontend check when required checks are configured individually.

## Verification

From a Node 20 shell (`node --version` must report `v20.x`), run:

```bash
rtk proxy node --version
rtk proxy sh -lc 'cd frontend && npm ci --cache "$(mktemp -d)"'
rtk make verify
```

This assumes the backend virtualenv is already installed as documented in `AGENTS.md`. Confirm the
test output includes a passing `__tests__/components/compliance-panel.test.tsx` suite. In CI, the
frontend job should run `make verify-frontend` after `npm ci`; the root `make verify` invocation here
also proves that the backend-plus-frontend aggregate is wired correctly.

For local developer installs where `node_modules` is absent, use:

```bash
rtk proxy sh -lc 'cd frontend && npm install --cache "$(mktemp -d)"'
```

## Acceptance Criteria

- GitHub Actions has distinct backend and frontend verification jobs.
- Frontend typecheck, lint, test, and build run on every pull request.
- The frontend job is merge-required wherever protected-branch rules require individual status
  checks; the existing backend required check remains intact.
- The frontend verification sequence passes on Node 20, including
  `__tests__/components/compliance-panel.test.tsx`; pdf.js remains isolated behind the existing
  jsdom test seam rather than requiring a production polyfill.
- Local docs distinguish `npm install --cache "$(mktemp -d)"` from CI `npm ci`.
- Root or documented commands make it obvious how to run frontend verification before claiming done.
