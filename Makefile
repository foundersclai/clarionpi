.PHONY: lint typecheck test hub-check verify verify-backend verify-frontend dev

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
