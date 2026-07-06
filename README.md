# ClarionPI

ClarionPI is an AI pipeline that turns personal-injury case files — medical records,
bills, police reports — into attorney-approved demand packages, with page-level
provenance back to source documents. It is backend-first and built for captive-firm
deployment (one firm's matters, one tenant, `firm_id` on every scoped table). The
founding design suite (vision, tech stack, data model, implementation plan, and more)
lives in the TMEPAgent repo at `backlog/pi` and is adopted as the architectural
baseline in [docs/adr/0001-adopt-pi-design-suite.md](docs/adr/0001-adopt-pi-design-suite.md).

## Quick Start

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e . --group dev
cd ..
cp .env.example .env   # then fill values — see Configuration
make dev
```

Verify it worked: `curl localhost:8400/healthz` → `{"status":"ok"}`.

## Commands

All build/test/lint commands live in [AGENTS.md](AGENTS.md) — single source of truth
for humans and agents. Don't duplicate them here.

## Architecture at a Glance

- Corpus ingest — case documents (medical records, bills, police reports) come in and
  are catalogued with provenance.
- Extraction — structured facts get pulled off each document, page-anchored.
- Fact registry spine — extracted facts land in a canonical registry other stages read
  from, rather than each stage re-parsing source documents.
- Gates G1–G3 — the orchestrator gate machine advances a matter through checkpoints,
  each with its own guards and invalidation rules.
- Brain-2 drafting — the demand narrative gets drafted from the fact registry, once
  gates are satisfied.
- Package builder — the drafted narrative plus supporting exhibits get assembled into
  the final demand package for attorney review and approval.

Decisions behind this shape live in [docs/adr/](docs/adr/).

## Configuration

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string | `postgresql://clarionpi:clarionpi_dev@localhost:5433/clarionpi` |
| `OBJECT_STORE_ENDPOINT` | yes | S3-compatible object store endpoint (MinIO in dev) | `http://localhost:9400` |
| `OBJECT_STORE_KEY` | yes | Object store access key | `clarionpi` |
| `OBJECT_STORE_SECRET` | yes | Object store secret key | `clarionpi_dev_secret` |
| `APP_ENV` | yes | Runtime environment | `dev` |
| `MATTER_BUDGET_DEFAULT_CENTS` | yes | Default per-matter spend ceiling, integer cents | `2500` |

Secrets come from `.env` (gitignored) in dev — never committed. `.env.example` lists
every variable with safe local-dev defaults.

## Troubleshooting

- `ECONNREFUSED` / connection refused talking to Postgres → the dev DB isn't running →
  `docker compose -f deploy/docker-compose.yml up -d db`.
