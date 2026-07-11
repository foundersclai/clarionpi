"""Liveness probe + fail-closed production boot (auth-hardening audit SEC-01).

The boot tests prove the production guard is actually INVOKED on both boot paths, not merely
defined in ``config.py``: the lifespan path (``with TestClient(app)`` runs startup) and the
module-construction path (a subprocess import with lifespan never entered — ``uvicorn
--lifespan off`` must not bypass the guard).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_healthz_returns_ok() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_refuses_invalid_production_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup (lifespan) must refuse prod+stub before serving anything."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("AUTH_MODE", "stub")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="AUTH_MODE=session"), TestClient(app) as booted:
            booted.get("/healthz")  # pragma: no cover - never reached
    finally:
        get_settings.cache_clear()


def test_module_construction_refuses_invalid_production_config() -> None:
    """A fresh `import app.main` under prod+stub fails even when lifespan never runs.

    This is the ``uvicorn --lifespan off`` scenario: the construction-time check must refuse
    invalid production auth settings before the FastAPI instance is exposed at all.
    """
    env = os.environ | {"APP_ENV": "prod", "AUTH_MODE": "stub"}
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "AUTH_MODE=session" in proc.stderr


def test_module_construction_accepts_valid_production_config() -> None:
    """The same import succeeds when production is configured fail-closed correctly."""
    env = os.environ | {
        "APP_ENV": "prod",
        "AUTH_MODE": "session",
        "SESSION_COOKIE_SECURE": "true",
        "CSRF_ENFORCE": "true",
        "CSRF_TRUSTED_ORIGINS": "https://app.example.com",
        # Prod refuses on-disk SQLite defaults nowhere yet; keep the default database URL.
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
