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
import tempfile
from dataclasses import dataclass
from functools import lru_cache

# Default per-matter AI spend cap (integer cents) when a matter has no explicit budget row.
_DEFAULT_MATTER_BUDGET_CENTS = 2500
_TEST_DATABASE_URL = "sqlite+pysqlite:///:memory:"
_DEV_DATABASE_URL = "sqlite:///./clarionpi_dev.db"
# Dev defaults for on-disk roots (relative to the backend working dir); under APP_ENV=test
# these move under the system tempdir so the suite never writes into the repo tree.
_DEV_STORAGE_ROOT = "./var/storage"
_DEV_MATTER_LOGS_DIR = "./logs/matters"


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings. Constructed once via :func:`get_settings`.

    Money is integer cents everywhere (``matter_budget_default_cents``) — the AGENTS
    currency boundary applies even to config defaults. The confidence/overlap floors are
    scores, not currency, so they are floats.
    """

    app_env: str
    database_url: str
    matter_budget_default_cents: int
    # Corpus ingest (M1). Defaulted so existing call sites that construct ``Settings`` with only
    # the three core fields keep working; ``get_settings`` fills all of them from the env.
    storage_backend: str = "local"
    storage_root: str = _DEV_STORAGE_ROOT
    upload_session_ttl_minutes: int = 1440
    # Upload registration limits (upload-safety audit SEC-05): a PI case-file batch is large
    # but bounded — 200 files, 100 MiB per file, 1 GiB per session. All three are validated
    # positive at settings load; the service layer enforces them before minting slots.
    upload_max_files_per_session: int = 200
    upload_max_bytes_per_file: int = 104_857_600
    upload_max_bytes_per_session: int = 1_073_741_824
    ocr_engine: str = "none"
    text_density_floor: int = 32
    classifier_model: str = "claude-haiku-4-5"
    classifier_sample_pages: int = 3
    classifier_confidence_floor: float = 0.7
    shingle_size: int = 5
    shingle_overlap_threshold: float = 0.35
    matter_logs_dir: str = _DEV_MATTER_LOGS_DIR
    # Extraction (M2). Per-stage models + window sizing + the per-call output-token ceiling.
    extractor_model: str = "claude-sonnet-5"
    narrative_model: str = "claude-sonnet-5"
    merge_tiebreak_model: str = "claude-sonnet-5"
    extraction_window_pages: int = 8
    extraction_window_overlap: int = 2
    llm_max_output_tokens: int = 4096
    # Auth (M3 Wave A). ``auth_mode`` selects the M0 dev-attorney stub ("stub", the dev/test
    # default) or real server-side session login ("session"). TTL bounds a session's lifetime; the
    # cookie name is constant-like but kept in Settings so tests can reference it without a literal.
    auth_mode: str = "stub"
    session_ttl_minutes: int = 720
    session_cookie_name: str = "clarionpi_session"
    # HTTPS-only session cookie (SEC-02). Defaults True in prod, False elsewhere; production
    # refuses an explicit false override (validate_runtime_settings — fail closed, no
    # break-glass).
    session_cookie_secure: bool = False
    # Risk flags (M4). GapDetectorConfig threshold: a treatment gap wider than this many days
    # (pre-MMI) flags. Firm-configurable later; a plain int day count, not money.
    treatment_gap_max_days: int = 30
    # Below this property-damage figure, any injury treatment trips a low-property-damage flag
    # ($1,500 default). Integer cents (money boundary); calibration pending (risk_flag_engine §8).
    low_property_damage_threshold_cents: int = 150000
    # Flood guard: at most this many flags surfaced per kind. Capped flags are still SURFACED,
    # never silently dropped — the cap bounds display volume, not the underlying findings.
    risk_flag_per_kind_cap: int = 12
    # Model for the LLM risk-labeling pass (risk_flag_engine).
    risk_label_model: str = "claude-sonnet-5"
    # Brain-2 / G3 model tiering (M5): the drafter (per-section prose) and the strategy memo run on
    # Opus (the intelligence/synthesis tier); the compliance judge runs on Sonnet (the semantic-
    # verdict grader). Per the design model-assignment tiering.
    drafter_model: str = "claude-opus-4-8"
    judge_model: str = "claude-sonnet-5"
    memo_model: str = "claude-opus-4-8"
    # Package build (M5 Wave B2): the Bates prefix stamped on binder pages (e.g. "CP00001"). A
    # short firm-scoped label, not money; deterministic input to the binder bytes.
    bates_prefix: str = "CP"


def _env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` when unset or blank."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` when unset or blank."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _positive_int(name: str, value: int) -> int:
    """Reject a zero/negative configured limit at settings load (fail at boot, not mid-upload)."""
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    """Read a bool env var STRICTLY: true/false/1/0/yes/no (case-insensitive), else raise.

    Python truthiness on env strings is a security hazard (``bool("false") is True``), so an
    unrecognized value is refused at settings load rather than silently coerced.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise ValueError(f"{name} must be true/false/1/0/yes/no, got {raw!r}")


# The full supported environment set. `test` is code-supported (config defaults +
# tests/api/conftest); `staging` receives dev-tier defaults today (seeding, cookie posture).
_VALID_APP_ENVS = frozenset({"dev", "test", "staging", "prod"})
_VALID_AUTH_MODES = frozenset({"stub", "session"})


def validate_runtime_settings(settings: Settings) -> None:
    """Refuse invalid or production-unsafe runtime settings (SEC-01/02) — fail closed.

    Called from ``app.main`` at module construction (so a production process refuses to
    build the app even when ASGI lifespan is disabled, e.g. ``uvicorn --lifespan off``) and
    again from the FastAPI lifespan before any startup work. Deliberately NOT called inside
    :func:`get_settings`, which stays side-effect free so tests can construct invalid
    settings on purpose.
    """
    if settings.app_env not in _VALID_APP_ENVS:
        raise ValueError(
            f"APP_ENV must be one of {sorted(_VALID_APP_ENVS)}, got {settings.app_env!r} — "
            "a typo (e.g. 'production') must not silently select dev security defaults"
        )
    if settings.auth_mode not in _VALID_AUTH_MODES:
        raise ValueError(
            f"AUTH_MODE must be one of {sorted(_VALID_AUTH_MODES)}, got {settings.auth_mode!r}"
        )
    if settings.app_env == "prod":
        if settings.auth_mode != "session":
            raise ValueError(
                "production cannot boot in stub auth: APP_ENV=prod requires AUTH_MODE=session"
            )
        if not settings.session_cookie_secure:
            raise ValueError(
                "production requires SESSION_COOKIE_SECURE=true (HTTPS-only session cookie); "
                "there is no break-glass override"
            )


def _default_database_url(app_env: str) -> str:
    """In-memory SQLite under ``APP_ENV=test``; file-backed dev database otherwise."""
    return _TEST_DATABASE_URL if app_env == "test" else _DEV_DATABASE_URL


def _default_storage_root(app_env: str) -> str:
    """A tempdir path under ``APP_ENV=test`` so tests never write into the repo tree."""
    if app_env == "test":
        return os.path.join(tempfile.gettempdir(), "clarionpi-test-storage")
    return _DEV_STORAGE_ROOT


def _default_matter_logs_dir(app_env: str) -> str:
    """A tempdir path under ``APP_ENV=test`` so tests never write into the repo tree."""
    if app_env == "test":
        return os.path.join(tempfile.gettempdir(), "clarionpi-test-matter-logs")
    return _DEV_MATTER_LOGS_DIR


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
        storage_backend=os.environ.get("STORAGE_BACKEND", "local"),
        storage_root=os.environ.get("STORAGE_ROOT") or _default_storage_root(app_env),
        upload_session_ttl_minutes=_env_int("UPLOAD_SESSION_TTL_MINUTES", 1440),
        upload_max_files_per_session=_positive_int(
            "UPLOAD_MAX_FILES_PER_SESSION", _env_int("UPLOAD_MAX_FILES_PER_SESSION", 200)
        ),
        upload_max_bytes_per_file=_positive_int(
            "UPLOAD_MAX_BYTES_PER_FILE", _env_int("UPLOAD_MAX_BYTES_PER_FILE", 104_857_600)
        ),
        upload_max_bytes_per_session=_positive_int(
            "UPLOAD_MAX_BYTES_PER_SESSION",
            _env_int("UPLOAD_MAX_BYTES_PER_SESSION", 1_073_741_824),
        ),
        ocr_engine=os.environ.get("OCR_ENGINE", "none"),
        text_density_floor=_env_int("TEXT_DENSITY_FLOOR", 32),
        classifier_model=os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5"),
        classifier_sample_pages=_env_int("CLASSIFIER_SAMPLE_PAGES", 3),
        classifier_confidence_floor=_env_float("CLASSIFIER_CONFIDENCE_FLOOR", 0.7),
        shingle_size=_env_int("SHINGLE_SIZE", 5),
        shingle_overlap_threshold=_env_float("SHINGLE_OVERLAP_THRESHOLD", 0.35),
        matter_logs_dir=os.environ.get("MATTER_LOGS_DIR") or _default_matter_logs_dir(app_env),
        extractor_model=os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-5"),
        narrative_model=os.environ.get("NARRATIVE_MODEL", "claude-sonnet-5"),
        merge_tiebreak_model=os.environ.get("MERGE_TIEBREAK_MODEL", "claude-sonnet-5"),
        extraction_window_pages=_env_int("EXTRACTION_WINDOW_PAGES", 8),
        extraction_window_overlap=_env_int("EXTRACTION_WINDOW_OVERLAP", 2),
        llm_max_output_tokens=_env_int("LLM_MAX_OUTPUT_TOKENS", 4096),
        auth_mode=os.environ.get("AUTH_MODE", "stub"),
        session_ttl_minutes=_env_int("SESSION_TTL_MINUTES", 720),
        session_cookie_name=os.environ.get("SESSION_COOKIE_NAME", "clarionpi_session"),
        session_cookie_secure=_env_bool("SESSION_COOKIE_SECURE", app_env == "prod"),
        treatment_gap_max_days=_env_int("TREATMENT_GAP_MAX_DAYS", 30),
        low_property_damage_threshold_cents=_env_int("LOW_PROPERTY_DAMAGE_THRESHOLD_CENTS", 150000),
        risk_flag_per_kind_cap=_env_int("RISK_FLAG_PER_KIND_CAP", 12),
        risk_label_model=os.environ.get("RISK_LABEL_MODEL", "claude-sonnet-5"),
        drafter_model=os.environ.get("DRAFTER_MODEL", "claude-opus-4-8"),
        judge_model=os.environ.get("JUDGE_MODEL", "claude-sonnet-5"),
        memo_model=os.environ.get("MEMO_MODEL", "claude-opus-4-8"),
        bates_prefix=os.environ.get("BATES_PREFIX", "CP"),
    )
