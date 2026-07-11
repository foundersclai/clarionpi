"""Settings: defaults, env overrides, and the APP_ENV=test tempdir defaults for on-disk roots.

Each test mutates ``os.environ`` via monkeypatch and clears the ``get_settings`` cache so it
re-reads. The M1 ingest fields are covered alongside the original core fields.
"""

from __future__ import annotations

import tempfile

import pytest

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_defaults_for_ingest_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear every ingest env var so we observe the code defaults, with a non-test app_env
    # (so on-disk roots take their dev defaults, not the tempdir ones).
    monkeypatch.setenv("APP_ENV", "dev")
    for name in (
        "STORAGE_BACKEND",
        "STORAGE_ROOT",
        "UPLOAD_SESSION_TTL_MINUTES",
        "OCR_ENGINE",
        "TEXT_DENSITY_FLOOR",
        "CLASSIFIER_MODEL",
        "CLASSIFIER_SAMPLE_PAGES",
        "CLASSIFIER_CONFIDENCE_FLOOR",
        "SHINGLE_SIZE",
        "SHINGLE_OVERLAP_THRESHOLD",
        "MATTER_LOGS_DIR",
        "EXTRACTOR_MODEL",
        "NARRATIVE_MODEL",
        "MERGE_TIEBREAK_MODEL",
        "EXTRACTION_WINDOW_PAGES",
        "EXTRACTION_WINDOW_OVERLAP",
        "LLM_MAX_OUTPUT_TOKENS",
        "TREATMENT_GAP_MAX_DAYS",
        "LOW_PROPERTY_DAMAGE_THRESHOLD_CENTS",
        "RISK_FLAG_PER_KIND_CAP",
        "RISK_LABEL_MODEL",
        "UPLOAD_MAX_FILES_PER_SESSION",
        "UPLOAD_MAX_BYTES_PER_FILE",
        "UPLOAD_MAX_BYTES_PER_SESSION",
    ):
        monkeypatch.delenv(name, raising=False)
    s = get_settings()
    assert s.storage_backend == "local"
    assert s.storage_root == "./var/storage"
    assert s.upload_session_ttl_minutes == 1440
    # Upload registration limits (SEC-05): bounded defaults, never unlimited.
    assert s.upload_max_files_per_session == 200
    assert s.upload_max_bytes_per_file == 104_857_600
    assert s.upload_max_bytes_per_session == 1_073_741_824
    assert s.ocr_engine == "none"
    assert s.text_density_floor == 32
    assert s.classifier_model == "claude-haiku-4-5"
    assert s.classifier_sample_pages == 3
    assert s.classifier_confidence_floor == pytest.approx(0.7)
    assert s.shingle_size == 5
    assert s.shingle_overlap_threshold == pytest.approx(0.35)
    assert s.matter_logs_dir == "./logs/matters"
    # M2 extraction defaults.
    assert s.extractor_model == "claude-sonnet-5"
    assert s.narrative_model == "claude-sonnet-5"
    assert s.merge_tiebreak_model == "claude-sonnet-5"
    assert s.extraction_window_pages == 8
    assert s.extraction_window_overlap == 2
    assert s.llm_max_output_tokens == 4096
    # M4 risk-flag defaults ($1,500 threshold = 150000 integer cents).
    assert s.treatment_gap_max_days == 30
    assert s.low_property_damage_threshold_cents == 150000
    assert s.risk_flag_per_kind_cap == 12
    assert s.risk_label_model == "claude-sonnet-5"


def test_env_overrides_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("STORAGE_ROOT", "/custom/root")
    monkeypatch.setenv("UPLOAD_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("OCR_ENGINE", "fake")
    monkeypatch.setenv("TEXT_DENSITY_FLOOR", "10")
    monkeypatch.setenv("CLASSIFIER_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("CLASSIFIER_SAMPLE_PAGES", "5")
    monkeypatch.setenv("CLASSIFIER_CONFIDENCE_FLOOR", "0.9")
    monkeypatch.setenv("SHINGLE_SIZE", "7")
    monkeypatch.setenv("SHINGLE_OVERLAP_THRESHOLD", "0.5")
    monkeypatch.setenv("MATTER_LOGS_DIR", "/custom/logs")
    monkeypatch.setenv("EXTRACTOR_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("NARRATIVE_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("MERGE_TIEBREAK_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("EXTRACTION_WINDOW_PAGES", "12")
    monkeypatch.setenv("EXTRACTION_WINDOW_OVERLAP", "3")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("TREATMENT_GAP_MAX_DAYS", "45")
    monkeypatch.setenv("LOW_PROPERTY_DAMAGE_THRESHOLD_CENTS", "200000")
    monkeypatch.setenv("RISK_FLAG_PER_KIND_CAP", "20")
    monkeypatch.setenv("RISK_LABEL_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("UPLOAD_MAX_FILES_PER_SESSION", "10")
    monkeypatch.setenv("UPLOAD_MAX_BYTES_PER_FILE", "1000")
    monkeypatch.setenv("UPLOAD_MAX_BYTES_PER_SESSION", "5000")
    s = get_settings()
    assert s.storage_backend == "s3"
    assert s.storage_root == "/custom/root"
    assert s.upload_session_ttl_minutes == 60
    assert s.ocr_engine == "fake"
    assert s.text_density_floor == 10
    assert s.classifier_model == "claude-sonnet-4-5"
    assert s.classifier_sample_pages == 5
    assert s.classifier_confidence_floor == pytest.approx(0.9)
    assert s.shingle_size == 7
    assert s.shingle_overlap_threshold == pytest.approx(0.5)
    assert s.matter_logs_dir == "/custom/logs"
    assert s.extractor_model == "claude-opus-4-8"
    assert s.narrative_model == "claude-haiku-4-5"
    assert s.merge_tiebreak_model == "claude-opus-4-8"
    assert s.extraction_window_pages == 12
    assert s.extraction_window_overlap == 3
    assert s.llm_max_output_tokens == 8192
    assert s.treatment_gap_max_days == 45
    assert s.low_property_damage_threshold_cents == 200000
    assert s.risk_flag_per_kind_cap == 20
    assert s.risk_label_model == "claude-opus-4-8"
    assert s.upload_max_files_per_session == 10
    assert s.upload_max_bytes_per_file == 1000
    assert s.upload_max_bytes_per_session == 5000


@pytest.mark.parametrize(
    "name",
    ["UPLOAD_MAX_FILES_PER_SESSION", "UPLOAD_MAX_BYTES_PER_FILE", "UPLOAD_MAX_BYTES_PER_SESSION"],
)
@pytest.mark.parametrize("bad", ["0", "-1"])
def test_zero_or_negative_upload_limits_are_rejected_at_load(
    monkeypatch: pytest.MonkeyPatch, name: str, bad: str
) -> None:
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=name):
        get_settings()


def test_test_env_defaults_on_disk_roots_under_tempdir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    monkeypatch.delenv("MATTER_LOGS_DIR", raising=False)
    s = get_settings()
    tmp = tempfile.gettempdir()
    assert s.storage_root.startswith(tmp)
    assert "clarionpi-test-storage" in s.storage_root
    assert s.matter_logs_dir.startswith(tmp)
    assert "clarionpi-test-matter-logs" in s.matter_logs_dir


# ---------------------------------------------------------------------------------------
# Production runtime validation (auth-hardening audit SEC-01/02)
# ---------------------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "prod",
        "database_url": "sqlite:///x.db",
        "matter_budget_default_cents": 2500,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_prod_with_stub_auth_is_refused() -> None:
    from app.core.config import validate_runtime_settings

    with pytest.raises(ValueError, match="AUTH_MODE=session"):
        validate_runtime_settings(_settings(auth_mode="stub", session_cookie_secure=True))


def test_prod_with_session_auth_passes() -> None:
    from app.core.config import validate_runtime_settings

    validate_runtime_settings(
        _settings(
            auth_mode="session",
            session_cookie_secure=True,
            csrf_enforce=True,
            csrf_trusted_origins=("https://app.example.com",),
        )
    )


def test_test_env_with_stub_default_passes() -> None:
    from app.core.config import validate_runtime_settings

    validate_runtime_settings(_settings(app_env="test", auth_mode="stub"))


def test_prod_with_insecure_cookie_is_refused() -> None:
    from app.core.config import validate_runtime_settings

    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        validate_runtime_settings(_settings(auth_mode="session", session_cookie_secure=False))


def test_invalid_auth_mode_is_refused() -> None:
    from app.core.config import validate_runtime_settings

    with pytest.raises(ValueError, match="AUTH_MODE"):
        validate_runtime_settings(_settings(app_env="dev", auth_mode="jwt"))


def test_invalid_app_env_is_refused() -> None:
    """An APP_ENV typo (e.g. 'production') must not silently bypass every exact-'prod' guard."""
    from app.core.config import validate_runtime_settings

    with pytest.raises(ValueError, match="APP_ENV"):
        validate_runtime_settings(_settings(app_env="production", auth_mode="session"))


def test_session_cookie_secure_defaults_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    assert get_settings().session_cookie_secure is False
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "prod")
    assert get_settings().session_cookie_secure is True


def test_strict_bool_parsing_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """bool('false') is True — the strict parser must refuse anything non-canonical."""
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "definitely")
    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        get_settings()


def test_prod_with_csrf_disabled_is_refused() -> None:
    from app.core.config import validate_runtime_settings

    with pytest.raises(ValueError, match="CSRF_ENFORCE"):
        validate_runtime_settings(
            _settings(
                auth_mode="session",
                session_cookie_secure=True,
                csrf_enforce=False,
                csrf_trusted_origins=("https://app.example.com",),
            )
        )


@pytest.mark.parametrize(
    "origins",
    [
        (),  # empty
        ("http://app.example.com",),  # not HTTPS
        ("https://*.example.com",),  # wildcard
        ("https://user:pw@app.example.com",),  # credentials
        ("https://app.example.com/path",),  # path-bearing
    ],
)
def test_prod_trusted_origin_shapes_are_refused(origins: tuple[str, ...]) -> None:
    from app.core.config import validate_runtime_settings

    with pytest.raises(ValueError):
        validate_runtime_settings(
            _settings(
                auth_mode="session",
                session_cookie_secure=True,
                csrf_enforce=True,
                csrf_trusted_origins=origins,
            )
        )


def test_prod_with_https_trusted_origin_passes() -> None:
    from app.core.config import validate_runtime_settings

    validate_runtime_settings(
        _settings(
            auth_mode="session",
            session_cookie_secure=True,
            csrf_enforce=True,
            csrf_trusted_origins=("https://app.example.com",),
        )
    )


def test_csrf_enforce_defaults_follow_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CSRF_ENFORCE", raising=False)
    monkeypatch.setenv("AUTH_MODE", "stub")
    assert get_settings().csrf_enforce is False
    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_MODE", "session")
    assert get_settings().csrf_enforce is True


def test_parse_origin_canonicalizes_and_rejects() -> None:
    from app.core.config import parse_origin

    assert parse_origin("HTTP://LocalHost:3400") == "http://localhost:3400"
    assert parse_origin("https://app.example.com") == "https://app.example.com"
    for bad in ("null", "*", "ftp://x", "https://a@b", "https://x/path", "", "x"):
        with pytest.raises(ValueError):
            parse_origin(bad)
