# ClarionPI — Agent Guide

ClarionPI turns a personal-injury case file (medical records, bills, police reports)
into an attorney-approved demand package with page-level provenance. Backend-first;
captive-firm deployment.

Read this before making changes. Where this file conflicts with your defaults, this
file wins.

## Commands

| Task | Command |
|---|---|
| Install deps (backend) | `cd backend && python3 -m venv .venv && .venv/bin/pip install -e . --group dev` |
| Run locally (backend) | `make dev` (`cd backend && .venv/bin/uvicorn app.main:app --reload --port 8400`) |
| Test (fast) | `make test` — run after every change |
| Test (full) | `make verify` — run before claiming done |
| Lint + format | `make lint` |
| Typecheck | `make typecheck` |
| Build (backend) | n/a (no build step) |
| Install deps (frontend) | `cd frontend && npm install --cache "$(mktemp -d)"` |
| Run locally (frontend) | `cd frontend && npm run dev` (Next.js dev server on port 3400) |
| Test (frontend) | `cd frontend && npm run test` (Vitest) |
| Lint (frontend) | `cd frontend && npm run lint` |
| Build (frontend) | `cd frontend && npm run build` |

If any of these fail on a clean checkout, fixing that is the first task.

## Repo Map

```
backend/app/api/        REST + SSE wire only — view-models in, view-models out; no business logic (+ auth/session login, role guards, gates envelope + submit at M3; G2a evidence VM + evidence/analysis routes at M4; G2.5/G3/package VMs + drafting/compliance/package routes at M5; M6 provenance routes — token→anchor lookup + app-served PHI-audited document blob)
backend/app/core/       config, db session, tenancy, audit, telemetry, budget
backend/app/models/     enums + pydantic schemas + ORM; every firm-scoped table carries firm_id
backend/app/engine/     orchestrator gate machine (G1-G3) + tokenizer registry (fact spine, M2) + brain1 (chronology + risk detectors/disposition, M4) + analysis composition (analysis_running→evidence_review, M4) + brain2 (plan/allocator + drafter + validator + renderer + memo + the drafting SSE run, M5) + compliance (G3 deterministic checks + Sonnet judge + corrections + finding lifecycle, M5)
backend/app/rules/      lawyer-audited YAML packs + deadline math (backend/app/rules/packs/ is data, not code)
backend/app/money/      ALL currency arithmetic — integer cents, floats banned (+ specials ledger M2 + G2a source-row edits M4)
backend/app/corpus/     document ingest (live M1: sessions, classify, OCR fallback, dedup, phase0 SSE) + extraction (live M2: windows, extractors, anchor-validation, merge)
backend/app/package/    demand package builder — manifest read-model (picks + PHI + EX-mint + blocking preview) M4; all four artifact builds (letter.docx/binder.pdf/chronology.xlsx/provenance_report.pdf) + continuous Bates + byte-deterministic + immutable ArtifactSet, live M5
backend/tests/          pytest suite, mirrors backend/app/
frontend/               Next.js 15 workbench (M3): login, matter dashboard, G1/G1.5 gate screens (app/, components/, lib/; Vitest tests in __tests__/)
docs/adr/               architecture decisions — read before changing architecture
docs/module_contracts/  per-module boundary contracts — read before changing a module's surface
systemflows/            Mermaid diagrams of the main business flows (as-built; keep in sync with enums + machine.py)
samples/                reference/calibration material (real demand letters, forms, PHI-safe data) — NEVER fixture source; see samples/README.md THE RULE
scripts/hub_check.py    drift gate between AGENTS.md/CONTRACTS.md and the actual repo tree
```

## Conventions

- Language & style: Python 3.11 (see Gotchas — target is 3.12 once the toolchain
  machine is upgraded), ruff (100-col line length) + mypy on `app/*`.
- All money is integer cents via `app/money` types — floats are banned for currency
  anywhere in the codebase.
- Every firm-scoped table carries `firm_id` — this is a captive multi-firm platform,
  not a single-tenant app.
- Module ownership and boundaries are recorded in `docs/module_contracts/` and tracked
  in `CONTRACTS.md`; `make hub-check` fails the build if they drift from the filesystem.
- Commits: conventional commits (`feat:`, `fix:`, `chore:`, ...). Small, single-purpose
  PRs.

## Boundaries

**Never, without explicit human approval:**
- Commit secrets, credentials, or `.env` files.
- Hand-edit `backend/alembic/versions/*` after they land, or any other generated file.
- Delete, skip, or weaken a failing test to get the suite green.
- Put PHI (real client data) in fixtures or tests — synthetic data only.
- Add an LLM call outside the metered client in `backend/app/core`.
- Write currency math outside `backend/app/money`.

**Always:**
- Run the fast tests (`make test`) after every change; the full suite (`make verify`)
  before claiming done.
- Add a regression test with every bug fix.
- Record architecture-level decisions as an ADR in `docs/adr/`.

## Working Rules

- Testing: [docs/testing-policy.md](docs/testing-policy.md)
- Debugging: [docs/debugging-policy.md](docs/debugging-policy.md)
- Review: [docs/code-review-checklist.md](docs/code-review-checklist.md)
- Done means: [docs/definition-of-done.md](docs/definition-of-done.md)

## Gotchas

- `make test` needs no services (SQLite in-memory); `deploy/docker-compose.yml` is only
  for integration/dev against real Postgres + MinIO.
- No `uv` on this machine at bootstrap time — the backend venv is plain
  `python3 -m venv backend/.venv` + pip, not uv-managed. Activate nothing; always go
  through the venv's binaries directly (`.venv/bin/...`) or the `make` targets, which
  already do this. If `uv` becomes available later, migrating is a mechanical swap of
  the Commands table and CI workflow, not a design change.
- This machine's `python3` is 3.11.5, not the 3.12 originally targeted — `pyproject.toml`
  (`requires-python`, ruff `target-version`, mypy `python_version`) and the CI workflow
  are pinned to 3.11 to match. Bump all three together if/when the toolchain moves to
  3.12; don't bump just one.
- Port 5433 (not 5432) for Postgres in `deploy/docker-compose.yml` and `.env.example`,
  to avoid colliding with any other local Postgres instance.
- `pip install -e . --group dev` (PEP 735 dependency groups) requires a reasonably
  recent pip (25.1+); this repo's venv has pip 26.1.2 and it works directly — no
  `pip install .[dev]`-style extras needed.
- Ingest defaults are fail-visible, not silent: `OCR_ENGINE` defaults to `none`, so
  image-only pages flag `zero_text` (set `OCR_ENGINE=tesseract` only if the binary is
  installed); and `LLM_PROVIDER=null` (the default) means document classification degrades
  to the review queue by design rather than blocking the pipeline.
- Extraction requires a live `LLM_PROVIDER`. With `null` (the default), classification degrades
  every doc to `other` + review, so the Phase-0 extraction stage skips it
  (`doc_type_not_extractable`) — by design, so the no-LLM path stays runnable. The ledger/registry
  sync then still runs (it uses no model): the registry always mints its always-on `[[AMT]]`
  payloads (grand billed + demand basis over the — possibly empty — billing set). To see facts
  actually extracted end-to-end, wire `LLM_PROVIDER=anthropic` (needs `ANTHROPIC_API_KEY`) or use
  a `ScriptedProvider` in tests.
- `AUTH_MODE` defaults to `stub` (the M0 dev-attorney: `make dev` and the pre-M3 tests need no
  login). Real login is `AUTH_MODE=session`; the seeded dev users (attorney/paralegal/admin, one
  per role) share the `dev-password` and are **non-prod only** (`seed_dev_users` refuses under
  `APP_ENV=prod`). In stub mode a valid session cookie still wins, so the FE can develop real logins
  against a stub backend. Session details live in [ADR-0004](docs/adr/0004-m3-auth-decisions.md).
- Frontend `npm install` writes to a shared global cache that can wedge on this machine — always
  install with a throwaway cache dir: `npm install --cache "$(mktemp -d)"` (as in the Commands
  table). The frontend uses plain npm; there is no pnpm/yarn lockfile.
