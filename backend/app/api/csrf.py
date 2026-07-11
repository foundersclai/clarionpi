"""Origin-check CSRF boundary for unsafe methods (auth-hardening audit SEC-03).

Session-cookie auth needs a CSRF control on every state-changing request. The check is the
browser ``Origin`` header (ADR: the workbench already sends same-origin requests through the
Next.js rewrite, and this avoids a session-table token column): for ``POST``/``PUT``/
``PATCH``/``DELETE``, exactly ONE ``Origin`` header must be present and its canonicalized
serialization must exactly match a configured trusted origin. Missing, duplicate, malformed,
``null``, and untrusted values are all refused with a typed ``403``
``{"error": "csrf_failed"}`` — including on login and logout (login CSRF would let an
attacker force a victim into an attacker-controlled session).

Enforcement is gated on ``settings.csrf_enforce`` (ON in session mode — including tests —
OFF in stub mode; production refuses OFF at boot). Implemented as PURE ASGI, not
``BaseHTTPMiddleware``: this app streams SSE responses, which BaseHTTPMiddleware buffers.
Settings are read per request so tests can flip modes without rebuilding the app.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings, parse_origin

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCsrfMiddleware:
    """Refuse unsafe-method requests whose Origin is not exactly one trusted origin."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        if not settings.csrf_enforce:
            await self.app(scope, receive, send)
            return
        origins = [value for name, value in scope["headers"] if name == b"origin"]
        if len(origins) != 1 or not self._is_trusted(origins[0], settings.csrf_trusted_origins):
            await _send_csrf_failed(send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _is_trusted(raw: bytes, trusted: tuple[str, ...]) -> bool:
        try:
            candidate = parse_origin(raw.decode("latin-1"))
        except ValueError:
            return False  # malformed / null / wildcard / credential- or path-bearing
        return candidate in trusted


async def _send_csrf_failed(send: Send) -> None:
    body = json.dumps({"error": "csrf_failed"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
