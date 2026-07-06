"""Runtime configuration — a frozen ``Settings`` read from the environment.

``pydantic-settings`` is intentionally *not* a dependency at M0 (it is not installed on
the bootstrap machine); config is read from :data:`os.environ` with stdlib only. Every
field has a default so a bare ``get_settings()`` works in tests and offline dev without any
env wiring.

The database default is environment-sensitive: ``APP_ENV=test`` gets an in-memory SQLite so
the suite never touches disk; any other env gets a file-backed dev database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

# Default per-matter AI spend cap (integer cents) when a matter has no explicit budget row.
_DEFAULT_MATTER_BUDGET_CENTS = 2500
_TEST_DATABASE_URL = "sqlite+pysqlite:///:memory:"
_DEV_DATABASE_URL = "sqlite:///./clarionpi_dev.db"


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings. Constructed once via :func:`get_settings`.

    Money is integer cents everywhere (``matter_budget_default_cents``) — the AGENTS
    currency boundary applies even to config defaults.
    """

    app_env: str
    database_url: str
    matter_budget_default_cents: int


def _env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` when unset or blank."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _default_database_url(app_env: str) -> str:
    """In-memory SQLite under ``APP_ENV=test``; file-backed dev database otherwise."""
    return _TEST_DATABASE_URL if app_env == "test" else _DEV_DATABASE_URL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`, cached for the lifetime of the process.

    ``lru_cache`` makes this a lazy singleton; tests that need a different environment clear
    the cache (``get_settings.cache_clear()``) after mutating ``os.environ``.
    """
    app_env = os.environ.get("APP_ENV", "dev")
    database_url = os.environ.get("DATABASE_URL") or _default_database_url(app_env)
    matter_budget_default_cents = _env_int(
        "MATTER_BUDGET_DEFAULT_CENTS", _DEFAULT_MATTER_BUDGET_CENTS
    )
    return Settings(
        app_env=app_env,
        database_url=database_url,
        matter_budget_default_cents=matter_budget_default_cents,
    )
