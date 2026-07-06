"""Wire token-scanner — nothing token-shaped ever leaves on a wire (invariant 11).

:func:`scan_wire_payload` recursively walks a response payload (dict / list / str) looking for
anything matching the registry's canonical token grammar (``[[FACT_n]]`` etc.,
:data:`~app.engine.tokenizer.registry.TOKEN_RE`). A match is a **bug** — resolution
(:func:`~app.engine.tokenizer.registry.resolve_text_for_wire`) should have replaced every token
before the payload was built — so the response is:

* **dev / test** (``settings.app_env != "prod"``): raise :class:`TokenLeak`. The leak surfaces
  as a 500 and fails CI loud — a silent scrub in development would hide the bug forever.
* **prod**: replace the match with the registry :data:`~app.engine.tokenizer.registry.SENTINEL`
  text, log ERROR on ``clarionpi.wire``, and return the scrubbed payload — an attorney-facing
  page must not 500 over a leak, but the leak is never silent (the sentinel is deliberately
  not token-shaped, so a scrubbed value can never re-parse as a token downstream).

Application is EXPLICIT at M3: the gates routes (and their view-model builders) call this on
every response envelope. M4+ plan: when the analysis/demand streams land, promote this to a
response middleware so every ``app.api`` payload passes through one scanner instead of
per-route calls.

Pure module: no DB, no FastAPI. Keys and values of dicts are both scanned (a token hiding in a
key is as much a leak as one in a value).
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.engine.tokenizer.registry import SENTINEL, TOKEN_RE

_LOG = logging.getLogger("clarionpi.wire")


class TokenLeak(Exception):
    """A token-shaped string reached a wire payload — a resolution bug, never user error.

    Carries ``where`` (which wire surface was being built) and the offending ``token`` so the
    failure names the leak precisely.
    """

    def __init__(self, *, where: str, token: str) -> None:
        self.where = where
        self.token = token
        super().__init__(f"token-shaped string {token!r} in wire payload at {where}")


def _scan_str(value: str, *, where: str, strict: bool) -> str:
    """Scan one string; raise (strict) or scrub-and-log (prod) on a token match."""
    match = TOKEN_RE.search(value)
    if match is None:
        return value
    if strict:
        raise TokenLeak(where=where, token=match.group(0))
    _LOG.error("token leak scrubbed at %s: %s", where, match.group(0))
    return TOKEN_RE.sub(SENTINEL, value)


def _scan(obj: object, *, where: str, strict: bool) -> object:
    """Recursive walk. Returns ``obj`` unchanged when clean; a scrubbed copy otherwise."""
    if isinstance(obj, str):
        return _scan_str(obj, where=where, strict=strict)
    if isinstance(obj, dict):
        return {
            _scan(key, where=where, strict=strict): _scan(value, where=where, strict=strict)
            for key, value in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_scan(item, where=where, strict=strict) for item in obj]
    # Non-container scalars (int, bool, None, UUID-as-str already handled above) cannot carry
    # a token; pass through untouched.
    return obj


def scan_wire_payload(obj: object, *, where: str) -> object:
    """Scan a wire payload for token-shaped strings; see the module doc for the two modes.

    ``where`` names the wire surface (e.g. ``"gates.current"``) for the raised error / the
    scrub log line. Returns the payload unchanged when clean.
    """
    strict = get_settings().app_env != "prod"
    return _scan(obj, where=where, strict=strict)
