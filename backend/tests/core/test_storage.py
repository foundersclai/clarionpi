"""Storage door: roundtrip, idempotent delete, missing-key error, key traversal, wiring.

All tests use a real ``LocalDiskStorage`` rooted at a pytest ``tmp_path`` — no network, no
repo writes. ``get_storage`` wiring is exercised with monkeypatched env + cache clears.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.storage import (
    InvalidStorageKey,
    LocalDiskStorage,
    StorageNotConfigured,
    StoredObjectNotFound,
    get_storage,
)


def test_put_get_exists_delete_roundtrip(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    assert store.exists("a/b.txt") is False
    store.put("a/b.txt", b"hello")
    assert store.exists("a/b.txt") is True
    assert store.get("a/b.txt") == b"hello"
    store.delete("a/b.txt")
    assert store.exists("a/b.txt") is False


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    # Deleting a never-stored key is a no-op, not an error.
    store.delete("never/stored.bin")
    store.put("x.bin", b"1")
    store.delete("x.bin")
    store.delete("x.bin")  # second delete also a no-op
    assert store.exists("x.bin") is False


def test_get_missing_raises_stored_object_not_found(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    with pytest.raises(StoredObjectNotFound):
        store.get("nope/missing.pdf")


def test_nested_keys_create_dirs(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    store.put("deep/nested/path/file.dat", b"payload")
    assert (tmp_path / "deep" / "nested" / "path" / "file.dat").read_bytes() == b"payload"


@pytest.mark.parametrize("bad_key", ["../escape.txt", "a/../../b.txt", "/abs/path", "a\\b", ""])
def test_key_traversal_attempts_rejected(tmp_path: Path, bad_key: str) -> None:
    store = LocalDiskStorage(tmp_path)
    with pytest.raises(InvalidStorageKey):
        store.put(bad_key, b"x")
    with pytest.raises(InvalidStorageKey):
        store.get(bad_key)


def test_presign_put_returns_none(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    assert store.presign_put("a/b.txt") is None


def test_presign_put_still_validates_key(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    with pytest.raises(InvalidStorageKey):
        store.presign_put("../escape.txt")


def test_get_storage_wires_local_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "store"))
    get_settings.cache_clear()
    get_storage.cache_clear()
    try:
        store = get_storage()
        assert isinstance(store, LocalDiskStorage)
        store.put("wired.txt", b"ok")
        assert store.get("wired.txt") == b"ok"
    finally:
        get_storage.cache_clear()
        get_settings.cache_clear()


def test_get_storage_unknown_backend_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    get_settings.cache_clear()
    get_storage.cache_clear()
    try:
        with pytest.raises(StorageNotConfigured) as exc:
            get_storage()
        assert "s3" in str(exc.value)
    finally:
        get_storage.cache_clear()
        get_settings.cache_clear()
