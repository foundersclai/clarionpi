"""Idempotency-key derivation + validation for gate submissions.

Double-submitting a gate action (network retry, double-click) must be a no-op returning the
first result (orchestrator_gates §6). The *uniqueness* is enforced by the DB constraint on
``gate_records`` (models wave, M1); this module only **derives** the canonical key and
**validates** the client-supplied component. No DB, no I/O here.

The derived key binds the client key to the ``(matter_id, gate)`` pair so the same client
key reused across different matters or gates does not collide.
"""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

from app.engine.orchestrator.errors import InvalidIdempotencyKey
from app.models.enums import GateState

_MIN_LEN = 8
_MAX_LEN = 128
_ALLOWED = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def validate_client_key(client_key: str) -> str:
    """Validate a client-supplied idempotency key; return it unchanged if valid.

    Rules: length in ``[8, 128]`` and characters restricted to ``[A-Za-z0-9._-]`` (URL- and
    header-safe; no whitespace or delimiters that could confuse the canonical triple).
    Raises ``InvalidIdempotencyKey`` (carrying the rejected key + reason) otherwise.
    """
    length = len(client_key)
    if length < _MIN_LEN or length > _MAX_LEN:
        raise InvalidIdempotencyKey(
            client_key,
            f"length {length} out of range [{_MIN_LEN}, {_MAX_LEN}]",
        )
    if not _ALLOWED.match(client_key):
        raise InvalidIdempotencyKey(
            client_key,
            "contains characters outside [A-Za-z0-9._-]",
        )
    return client_key


def derive_key(matter_id: UUID, gate: GateState, client_key: str) -> str:
    """Derive the canonical idempotency key: sha256 hex of ``(matter_id, gate, client_key)``.

    The client component is validated first (raises ``InvalidIdempotencyKey`` on bad input).
    Deterministic — the same triple always yields the same hex digest — and distinct across
    gates/matters because both are part of the hashed input. The digest is what the DB
    unique constraint keys on at M1.
    """
    validate_client_key(client_key)
    canonical = f"{matter_id}\x1f{gate.value}\x1f{client_key}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
