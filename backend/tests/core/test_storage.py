"""Storage door: roundtrip, idempotent delete, missing-key error, key traversal, wiring.

All tests use a real ``LocalDiskStorage`` rooted at a pytest ``tmp_path`` — no network, no
repo writes. ``get_storage`` wiring is exercised with monkeypatched env + cache clears.
"""

from __future__ import annotations

import io
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


# ---------------------------------------------------------------------------------------
# Staged file-object replacement (upload-safety audit SEC-05)
# ---------------------------------------------------------------------------------------


def _litter(tmp_path: Path) -> list[Path]:
    """Every staging/backup remnant under the storage root (must be empty after each op)."""
    return [
        p
        for p in tmp_path.rglob("*")
        if p.is_file() and (".staging-" in p.name or ".backup-" in p.name)
    ]


def test_stage_promote_finalize_first_write(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    staged = store.stage_fileobj("a/b.bin", io.BytesIO(b"NEW"))
    # Staging alone does not create the live object.
    assert store.exists("a/b.bin") is False
    staged.promote()
    assert store.get("a/b.bin") == b"NEW"
    staged.finalize()
    assert store.get("a/b.bin") == b"NEW"
    assert _litter(tmp_path) == []


def test_stage_promote_replaces_and_finalize_discards_backup(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    store.put("k.bin", b"OLD")
    staged = store.stage_fileobj("k.bin", io.BytesIO(b"NEW"))
    assert store.get("k.bin") == b"OLD"  # live object untouched while staged
    staged.promote()
    assert store.get("k.bin") == b"NEW"  # atomic swap
    assert len(_litter(tmp_path)) == 1  # the backup survives until finalize
    staged.finalize()
    assert _litter(tmp_path) == []


def test_rollback_before_promote_discards_staging_only(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    store.put("k.bin", b"OLD")
    staged = store.stage_fileobj("k.bin", io.BytesIO(b"NEW"))
    staged.rollback()
    assert store.get("k.bin") == b"OLD"
    assert _litter(tmp_path) == []


def test_rollback_after_promote_restores_prior_object(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    store.put("k.bin", b"OLD")
    staged = store.stage_fileobj("k.bin", io.BytesIO(b"NEW"))
    staged.promote()
    assert store.get("k.bin") == b"NEW"
    staged.rollback()
    assert store.get("k.bin") == b"OLD"
    assert _litter(tmp_path) == []


def test_rollback_after_promote_first_write_removes_object(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    staged = store.stage_fileobj("k.bin", io.BytesIO(b"NEW"))
    staged.promote()
    assert store.exists("k.bin") is True
    staged.rollback()
    assert store.exists("k.bin") is False
    assert _litter(tmp_path) == []


def test_staged_operations_are_idempotent(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    store.put("k.bin", b"OLD")
    staged = store.stage_fileobj("k.bin", io.BytesIO(b"NEW"))
    staged.promote()
    staged.promote()  # no-op
    staged.finalize()
    staged.finalize()  # no-op
    staged.rollback()  # after finalize: no-op — the swap is permanent
    assert store.get("k.bin") == b"NEW"
    assert _litter(tmp_path) == []
    # And rollback twice from the staged state is safe too.
    staged2 = store.stage_fileobj("k.bin", io.BytesIO(b"X"))
    staged2.rollback()
    staged2.rollback()
    assert store.get("k.bin") == b"NEW"


def test_stage_copy_failure_cleans_staging_and_leaves_dest(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    store.put("k.bin", b"OLD")

    class ExplodingReader(io.RawIOBase):
        def readinto(self, b: bytearray) -> int:  # pragma: no cover - signature only
            raise OSError("disk gone")

        def readable(self) -> bool:
            return True

    with pytest.raises(OSError):
        store.stage_fileobj("k.bin", io.BufferedReader(ExplodingReader()))
    assert store.get("k.bin") == b"OLD"
    assert _litter(tmp_path) == []


def test_stage_reads_from_current_file_position(tmp_path: Path) -> None:
    store = LocalDiskStorage(tmp_path)
    fileobj = io.BytesIO(b"SKIPPEDpayload")
    fileobj.seek(7)
    staged = store.stage_fileobj("k.bin", fileobj)
    staged.promote()
    staged.finalize()
    assert store.get("k.bin") == b"payload"
