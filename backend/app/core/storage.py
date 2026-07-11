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

import shutil
import uuid
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import IO, Protocol, runtime_checkable

from app.core.config import get_settings


class StoredObjectNotFound(Exception):
    """Raised by :meth:`ObjectStorage.get` when a key has no stored object."""


class InvalidStorageKey(Exception):
    """Raised when a storage key is not a safe relative POSIX path."""


class StorageNotConfigured(Exception):
    """Raised by :func:`get_storage` for a backend that is not implemented."""


class StagedObjectReplacement(Protocol):
    """A staged object replacement, coordinating a blob swap with a DB commit (SEC-05).

    Returned by :meth:`ObjectStorage.stage_fileobj`. All three operations are idempotent:

    - ``promote()`` atomically installs the staged object at the destination, retaining
      enough backup state to restore any pre-existing object.
    - ``rollback()`` undoes everything: pre-promotion it discards the staged copy; post-
      promotion it restores the prior object (or removes a first upload's object).
    - ``finalize()`` discards the recovery state — call ONLY after the DB commit succeeds.
    """

    def promote(self) -> None:
        """Atomically install the staged object at the destination key."""
        ...

    def rollback(self) -> None:
        """Restore the pre-staging state (prior object back, or no object at all)."""
        ...

    def finalize(self) -> None:
        """Discard recovery state after the coordinating DB commit succeeded."""
        ...


@runtime_checkable
class ObjectStorage(Protocol):
    """The storage port: put/get/exists/delete plus an optional presign."""

    def put(self, key: str, data: bytes) -> None:
        """Store ``data`` at ``key`` (creating any parent structure)."""
        ...

    def stage_fileobj(self, key: str, fileobj: IO[bytes]) -> StagedObjectReplacement:
        """Stage a replacement for ``key`` from ``fileobj`` (read from its current position)
        WITHOUT touching the live object; the returned handle promotes/rolls back/finalizes.
        Never reads a prior blob back into memory.
        """
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


class _LocalStagedReplacement:
    """Staged replacement for :class:`LocalDiskStorage` using sibling temp/backup files.

    The staging and backup files live in the destination's own directory (same filesystem,
    so ``os.replace`` is atomic). State machine: ``staged → promoted → finalized`` with
    ``rollback`` legal from ``staged`` or ``promoted``; every operation is idempotent.
    """

    def __init__(self, dest: Path, staging: Path) -> None:
        self._dest = dest
        self._staging = staging
        self._backup = dest.with_name(f"{dest.name}.backup-{uuid.uuid4().hex}")
        self._had_prior = False
        self._state = "staged"

    def promote(self) -> None:
        if self._state != "staged":
            return  # idempotent (already promoted / rolled back / finalized)
        self._had_prior = self._dest.is_file()
        try:
            if self._had_prior:
                self._dest.replace(self._backup)
            self._staging.replace(self._dest)
        except OSError:
            # Failed promotion leaves the pre-existing object in place and no temp litter.
            if self._had_prior and self._backup.is_file():
                self._backup.replace(self._dest)
            self._staging.unlink(missing_ok=True)
            self._state = "rolled_back"
            raise
        self._state = "promoted"

    def rollback(self) -> None:
        if self._state == "staged":
            self._staging.unlink(missing_ok=True)
            self._state = "rolled_back"
        elif self._state == "promoted":
            if self._had_prior:
                self._backup.replace(self._dest)  # restore the prior object
            else:
                self._dest.unlink(missing_ok=True)  # first upload: no object again
            self._state = "rolled_back"
        # finalized / rolled_back: idempotent no-op

    def finalize(self) -> None:
        if self._state == "promoted":
            self._backup.unlink(missing_ok=True)
            self._state = "finalized"
        # any other state: idempotent no-op (nothing to discard)


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

    def stage_fileobj(self, key: str, fileobj: IO[bytes]) -> StagedObjectReplacement:
        dest = _safe_relative_path(self._root, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = dest.with_name(f"{dest.name}.staging-{uuid.uuid4().hex}")
        try:
            with staging.open("wb") as out:
                shutil.copyfileobj(fileobj, out)
        except BaseException:
            staging.unlink(missing_ok=True)  # failed staging leaves no litter, dest untouched
            raise
        return _LocalStagedReplacement(dest, staging)

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
