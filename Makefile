.PHONY: lint typecheck test hub-check verify verify-backend verify-frontend dev \
	workshop workshop-backend workshop-frontend workshop-up workshop-scenario workshop-reset

lint:
	cd backend && .venv/bin/ruff check .
	cd backend && .venv/bin/ruff format --check .

typecheck:
	cd backend && .venv/bin/mypy app

test:
	cd backend && .venv/bin/pytest -q -m "not integration"

hub-check:
	python3 scripts/hub_check.py

# The backend-only aggregate (what the backend CI job runs — it installs no frontend deps).
verify-backend: lint typecheck test hub-check

# The frontend gate (OTH-01). Requires frontend deps installed first (local:
# `cd frontend && npm install --cache "$$(mktemp -d)"`; CI: `npm ci`) — the Makefile
# deliberately does NOT install dependencies.
verify-frontend:
	cd frontend && npm run typecheck
	cd frontend && npm run lint
	cd frontend && npm run test -- --run
	cd frontend && npm run build

# The repository's single done command: backend AND frontend.
verify: verify-backend verify-frontend

dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8400 --no-proxy-headers

# --- Workshop (operator-led MVP demo) ----------------------------------------
# A disposable, synthetic-only local environment for the operator-led attorney demo
# (backlog/planned/workshop_demo_milestone.md). Dedicated ports — backend 8001,
# frontend 3001 — so it never collides with `make dev` / `npm run dev` (8400 / 3400)
# or other local projects. Isolated + disposable: its own database, storage root, and
# matter-log dir, all wiped by `make workshop-reset`.
#
#   Fresh rehearsal : make workshop-reset && make workshop-scenario
#   Both servers    : make workshop-up          (one terminal; Ctrl-C stops both)
#   Separate logs   : make workshop-backend  |  make workshop-frontend   (two terminals)
#
# Session auth is on so the demo shows a real login; the seeded dev users
# (attorney/paralegal/admin) share the password `dev-password`. To pre-run the
# model-heavy analysis beats, put your key in backend/.env (gitignored; copy from
# backend/.env.example) — `make workshop-backend`/`workshop-up` source it before uvicorn.
# Absent or blank, the provider stays null and only the deterministic (offline) beats run.
WORKSHOP_BE_PORT ?= 8001
WORKSHOP_FE_PORT ?= 3001
WORKSHOP_AUTH_MODE ?= session

# Isolated, disposable runtime roots (relative to backend/, matching `make dev`'s layout).
WORKSHOP_DB ?= sqlite:///./clarionpi_workshop.db
WORKSHOP_STORAGE ?= ./var/workshop-storage
WORKSHOP_LOGS ?= ./logs/workshop-matters

# Env shared by every workshop backend invocation. CSRF trusts the 3001 frontend origin —
# the Next rewrite forwards the browser Origin header through to the backend.
WORKSHOP_BE_ENV = APP_ENV=dev \
	AUTH_MODE=$(WORKSHOP_AUTH_MODE) \
	DATABASE_URL=$(WORKSHOP_DB) \
	STORAGE_ROOT=$(WORKSHOP_STORAGE) \
	MATTER_LOGS_DIR=$(WORKSHOP_LOGS) \
	CSRF_TRUSTED_ORIGINS=http://localhost:$(WORKSHOP_FE_PORT),http://localhost:$(WORKSHOP_BE_PORT)

workshop:
	@echo "ClarionPI workshop demo — disposable, synthetic-only local environment"
	@echo "  backend  : http://localhost:$(WORKSHOP_BE_PORT)   (uvicorn, session auth)"
	@echo "  frontend : http://localhost:$(WORKSHOP_FE_PORT)   (Next.js; proxies /api -> backend)"
	@echo ""
	@echo "  make workshop-reset      wipe the disposable DB + storage + logs (fresh rehearsal)"
	@echo "  make workshop-scenario   render the 8 synthetic Rivas v. Doyle PDFs to upload"
	@echo "  make workshop-up         run backend + frontend together (Ctrl-C stops both)"
	@echo "  make workshop-backend    run only the backend on :$(WORKSHOP_BE_PORT)"
	@echo "  make workshop-frontend   run only the frontend on :$(WORKSHOP_FE_PORT)"
	@echo ""
	@echo "  login: seeded users (attorney/paralegal/admin) share password 'dev-password'"
	@echo "  pre-run model beats: put your key in backend/.env (cp backend/.env.example backend/.env)"

# backend/.env (gitignored) supplies LLM_PROVIDER + ANTHROPIC_API_KEY when present; it is
# sourced here (the backend reads os.environ directly — no dotenv auto-load). A blank or
# absent file leaves the provider null (offline) rather than erroring.
workshop-backend:
	cd backend && { [ -f .env ] && { set -a; . ./.env; set +a; } || true; } && \
		exec env $(WORKSHOP_BE_ENV) .venv/bin/uvicorn app.main:app --reload --port $(WORKSHOP_BE_PORT) --no-proxy-headers

workshop-frontend:
	cd frontend && CLARIONPI_BACKEND_ORIGIN=http://127.0.0.1:$(WORKSHOP_BE_PORT) ./node_modules/.bin/next dev -p $(WORKSHOP_FE_PORT)

workshop-up:
	@echo "workshop: backend :$(WORKSHOP_BE_PORT) + frontend :$(WORKSHOP_FE_PORT) — Ctrl-C stops both"
	@$(MAKE) -j2 workshop-backend workshop-frontend

workshop-scenario:
	cd backend && .venv/bin/python ../workshop/scenarios/az_mva_01/generate.py

# Wipe the disposable workshop runtime (default paths only). Never touches `make dev`'s
# clarionpi_dev.db / var/storage / logs/matters. Synthetic data only — nothing to preserve.
workshop-reset:
	@echo "Wiping the disposable workshop database, storage, and matter logs (synthetic only)…"
	rm -f  backend/clarionpi_workshop.db backend/clarionpi_workshop.db-wal backend/clarionpi_workshop.db-shm
	rm -rf backend/var/workshop-storage backend/logs/workshop-matters
	@echo "Done — the next 'make workshop-backend' boots a clean, freshly seeded database."
