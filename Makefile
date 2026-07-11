.PHONY: lint typecheck test hub-check verify dev

lint:
	cd backend && .venv/bin/ruff check .
	cd backend && .venv/bin/ruff format --check .

typecheck:
	cd backend && .venv/bin/mypy app

test:
	cd backend && .venv/bin/pytest -q -m "not integration"

hub-check:
	python3 scripts/hub_check.py

verify: lint typecheck test hub-check

dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8400 --no-proxy-headers
