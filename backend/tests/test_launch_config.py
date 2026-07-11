"""Launch-config invariant: Uvicorn proxy-header rewriting stays DISABLED (SEC-04).

The application helper (``app.core.auth_throttle.resolve_client_ip``) is the sole owner of
forwarded-client resolution. Uvicorn defaults ``proxy_headers=True`` and wraps the app in
``ProxyHeadersMiddleware``, which rewrites ``scope["client"]`` before FastAPI sees the
request — destroying the immediate-peer evidence the trust decision requires. Every
documented/supported launch command must therefore pass ``--no-proxy-headers``; otherwise
the tests would validate a trust model production bypasses before the request arrives.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _uvicorn_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "uvicorn" in line and "app.main:app" in line]


def test_makefile_dev_launch_disables_proxy_headers() -> None:
    lines = _uvicorn_lines((_REPO_ROOT / "Makefile").read_text())
    assert lines, "expected a uvicorn launch line in the Makefile"
    for line in lines:
        assert "--no-proxy-headers" in line, f"uvicorn launch missing --no-proxy-headers: {line}"


def test_agents_md_documented_launch_disables_proxy_headers() -> None:
    lines = _uvicorn_lines((_REPO_ROOT / "AGENTS.md").read_text())
    assert lines, "expected the documented raw uvicorn command in AGENTS.md"
    for line in lines:
        assert "--no-proxy-headers" in line, f"documented launch missing --no-proxy-headers: {line}"


def test_no_launch_config_relies_on_forwarded_allow_ips() -> None:
    """FORWARDED_ALLOW_IPS re-enables server-level parsing — the app helper must stay sole owner."""
    for path in (_REPO_ROOT / "Makefile", _REPO_ROOT / "AGENTS.md", _REPO_ROOT / ".env.example"):
        assert not re.search(r"FORWARDED_ALLOW_IPS", path.read_text()), (
            f"{path.name} must not configure FORWARDED_ALLOW_IPS"
        )
