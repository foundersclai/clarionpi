"""The object-store door — the single sanctioned path to case blobs.

Mirrors the ``llm_provider`` door philosophy: no other module touches disk or S3 for case
blobs, so the storage backend is swappable (local disk in dev/test, S3/MinIO in prod) without
any caller change. A backend is anything that satisfies :class:`ObjectStorage`.

Key discipline: a storage key is a *relative POSIX path*. Absolute paths, ``..`` traversal,
backslashes, and empty keys are rejected (:class:`InvalidStorageKey`) so a crafted key can
never escape the storage root. :class:`LocalDiskStorage` enforces this and is unit-tested for
each escape shape.

At M1 only the ``local`` backend exists; :func:`get_storage` raises
:class:`StorageNotConfigured` for anything else (S3/MinIO lands with the prod account).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from app.core.config import get_settings


class StoredObjectNotFound(Exception):
    """Raised by :meth:`ObjectStorage.get` when a key has no stored object."""


class InvalidStorageKey(Exception):
    """Raised when a storage key is not a safe relative POSIX path."""


class StorageNotConfigured(Exception):
    """Raised by :func:`get_storage` for a backend that is not implemented."""


@runtime_checkable
class ObjectStorage(Protocol):
    """The storage port: put/get/exists/delete plus an optional presign."""

    def put(self, key: str, data: bytes) -> None:
        """Store ``data`` at ``key`` (creating any parent structure)."""
        ...

    def get(self, key: str) -> bytes:
        """Return the bytes at ``key``; raise :class:`StoredObjectNotFound` if absent."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether an object is stored at ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Delete the object at ``key``. Idempotent: a missing key is a no-op."""
        ...

    def presign_put(self, key: str) -> str | None:
        """A presigned upload URL, or ``None`` if the backend cannot presign.

        ``None`` tells the caller to offer an app-mediated upload route instead of a direct
        client PUT.
        """
        ...


def _safe_relative_path(root: Path, key: str) -> Path:
    """Resolve ``key`` under ``root``, rejecting anything that is not a safe relative key.

    Rejects empty keys, absolute paths, backslashes, ``..`` segments, and any key whose
    resolved path escapes ``root``.
    """
    if not key or key.strip() == "":
        raise InvalidStorageKey("storage key must be a non-empty relative path")
    if "\\" in key:
        raise InvalidStorageKey(f"storage key must use POSIX separators, not backslashes: {key!r}")
    pure = PurePosixPath(key)
    if pure.is_absolute():
        raise InvalidStorageKey(f"storage key must be relative, got absolute: {key!r}")
    if any(part == ".." for part in pure.parts):
        raise InvalidStorageKey(f"storage key must not contain '..' segments: {key!r}")
    root_resolved = root.resolve()
    candidate = (root_resolved / pure).resolve()
    # Defence in depth: even after the checks above, confirm the resolved path is under root.
    if root_resolved != candidate and root_resolved not in candidate.parents:
        raise InvalidStorageKey(f"storage key escapes the storage root: {key!r}")
    return candidate


class LocalDiskStorage:
    """Dev/test backend: keys map to files under a root dir.

    ``presign_put`` returns ``None`` — the API layer serves a slot-addressed PUT route instead
    (the dev "presign"). All key access goes through :func:`_safe_relative_path`, so traversal
    outside the root is impossible.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> None:
        path = _safe_relative_path(self._root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = _safe_relative_path(self._root, key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StoredObjectNotFound(key) from exc

    def exists(self, key: str) -> bool:
        path = _safe_relative_path(self._root, key)
        return path.is_file()

    def delete(self, key: str) -> None:
        path = _safe_relative_path(self._root, key)
        path.unlink(missing_ok=True)  # idempotent: a missing key is a no-op

    def presign_put(self, key: str) -> str | None:
        # Validate the key so a bad key fails the same way it would on put/get, then decline
        # presigning: the local backend has no URL to hand out.
        _safe_relative_path(self._root, key)
        return None


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    """Return the process-wide :class:`ObjectStorage`, chosen by settings.

    ``local`` → :class:`LocalDiskStorage` rooted at ``settings.storage_root``. Any other
    backend raises :class:`StorageNotConfigured` naming it (S3/MinIO is not wired at M1).
    Cached like ``get_settings``; tests clear it (``get_storage.cache_clear()``) after
    mutating the environment, or construct :class:`LocalDiskStorage` directly.
    """
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalDiskStorage(settings.storage_root)
    raise StorageNotConfigured(
        f"storage backend {settings.storage_backend!r} is not implemented at M1 (only 'local')"
    )
