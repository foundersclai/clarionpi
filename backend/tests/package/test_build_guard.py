"""Audited-rule-pack package gate (BUS-02) — build_artifact_set authority + pin checks.

Reuses the ``test_build`` fixtures/helpers (same in-memory engine style). The shipped AZ pack
is an UNAUDITED stub, so with the guard enabled a build must refuse typed
(``RulePackUnaudited``) BEFORE the reuse lookup and leave storage/rows/audit untouched; a
matter pinned to the unaudited pack stays blocked even after the YAML is later made
authoritative (``RulePackChanged`` — re-labeling mutable law cannot retroactively authorize
prior work); missing pins fail closed; and ``APP_ENV=prod`` enforces even when a false
override is injected with no lifespan validation run.
"""

# ruff: noqa: F811 - test params intentionally shadow the fixtures imported from test_build
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.storage import LocalDiskStorage
from app.models.orm import ArtifactSet, AuditEvent, Matter, User
from app.package import build as build_mod
from app.rules import loader as loader_mod
from app.rules.errors import RulePackChanged, RulePackUnaudited, RulePackUnpinned
from app.rules.loader import load_pack

from .test_build import (  # noqa: F401 - fixtures wired by import
    _happy_matter,
    attorney,
    db,
    engine,
    firm,
    matter,
    session_factory,
    storage,
)


@pytest.fixture
def guard_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("REQUIRE_AUDITED_RULE_PACK_FOR_PACKAGE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def pinned_matter(db: Session, matter: Matter) -> Matter:
    """The matter pinned to the CURRENT (unaudited stub) AZ pack, as creation would."""
    pack = load_pack("AZ")
    matter.rule_pack_version = pack.version
    matter.rule_pack_fingerprint = pack.fingerprint
    db.commit()
    return matter


def _storage_files(store: LocalDiskStorage) -> list[Path]:
    root = Path(store._root)  # test-only peek
    return [p for p in root.rglob("*") if p.is_file()]


def _assert_no_side_effects(db: Session, store: LocalDiskStorage) -> None:
    assert db.scalars(select(ArtifactSet)).all() == []
    assert not any(e.event_kind == "artifact_set_built" for e in db.scalars(select(AuditEvent)))
    # No artifact objects were stored (exhibit source blobs from the fixture are allowed).
    assert not any("artifacts/" in str(p) for p in _storage_files(store))


def test_guard_off_unaudited_pack_builds_and_logs_diagnostic(
    db: Session,
    storage: LocalDiskStorage,
    pinned_matter: Matter,
    attorney: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Diagnostic evidence (debugging policy): with the guard OFF (dev/test default), the
    unaudited pack builds successfully and the non-PHI debug event records that fact."""
    draft = _happy_matter(db, storage, pinned_matter, attorney)
    with caplog.at_level("DEBUG", logger="clarionpi.package"):
        result = build_mod.build_artifact_set(
            db, storage, matter=pinned_matter, draft=draft, user=attorney, firm_name="F"
        )
    assert result.reused is False
    diag = [r.getMessage() for r in caplog.records if "package_build_authority" in r.getMessage()]
    assert len(diag) == 1
    assert "pack_audited=False" in diag[0]
    assert "guard_enabled=False" in diag[0]


def test_guard_on_unaudited_pack_refuses_before_any_side_effect(
    guard_on: None,
    db: Session,
    storage: LocalDiskStorage,
    pinned_matter: Matter,
    attorney: User,
) -> None:
    draft = _happy_matter(db, storage, pinned_matter, attorney)
    with pytest.raises(RulePackUnaudited) as exc:
        build_mod.build_artifact_set(
            db, storage, matter=pinned_matter, draft=draft, user=attorney, firm_name="F"
        )
    assert exc.value.jurisdiction == "AZ"
    _assert_no_side_effects(db, storage)
    assert pinned_matter.gate_state == "package_assembly"  # unchanged


def test_guard_on_runs_before_the_reuse_fast_path(
    db: Session,
    storage: LocalDiskStorage,
    pinned_matter: Matter,
    attorney: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-built set is NOT re-presented once the pack fails authority."""
    draft = _happy_matter(db, storage, pinned_matter, attorney)
    built = build_mod.build_artifact_set(
        db, storage, matter=pinned_matter, draft=draft, user=attorney, firm_name="F"
    )
    assert built.reused is False
    monkeypatch.setenv("REQUIRE_AUDITED_RULE_PACK_FOR_PACKAGE", "true")
    get_settings.cache_clear()
    try:
        with pytest.raises(RulePackUnaudited):
            build_mod.build_artifact_set(
                db, storage, matter=pinned_matter, draft=draft, user=attorney, firm_name="F"
            )
    finally:
        get_settings.cache_clear()


def test_missing_pin_fails_closed_when_guard_on(
    guard_on: None,
    db: Session,
    storage: LocalDiskStorage,
    matter: Matter,
    attorney: User,
) -> None:
    draft = _happy_matter(db, storage, matter, attorney)  # legacy: no pins set
    with pytest.raises(RulePackUnpinned):
        build_mod.build_artifact_set(
            db, storage, matter=matter, draft=draft, user=attorney, firm_name="F"
        )
    _assert_no_side_effects(db, storage)


def _authoritative_pack() -> loader_mod.RulePack:
    import datetime as dt

    data = load_pack("AZ").model_dump(mode="json")
    data.update(
        audited=True,
        audited_by="Legal Cofounder, Esq.",
        audited_at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC).isoformat(),
        audit_reference="audit-memo-2026-07-01",
    )
    for rule in data["deadline_rules"]:
        rule["verify_status"] = "verified"
    data["billed_vs_paid"]["verify_status"] = "verified"
    data["letter_structure"]["verify_status"] = "verified"
    return loader_mod.RulePack.model_validate(data)


def test_pack_audited_after_creation_cannot_retroactively_authorize(
    guard_on: None,
    db: Session,
    storage: LocalDiskStorage,
    pinned_matter: Matter,
    attorney: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The matter pinned to the unaudited pack stays blocked after the YAML flips to
    authoritative: its fingerprint no longer matches the pin (RulePackChanged)."""
    draft = _happy_matter(db, storage, pinned_matter, attorney)
    monkeypatch.setattr(loader_mod, "load_pack", lambda _j: _authoritative_pack())
    with pytest.raises(RulePackChanged):
        build_mod.build_artifact_set(
            db, storage, matter=pinned_matter, draft=draft, user=attorney, firm_name="F"
        )
    _assert_no_side_effects(db, storage)


def test_authoritative_pack_matching_pin_builds(
    guard_on: None,
    db: Session,
    storage: LocalDiskStorage,
    matter: Matter,
    attorney: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matter created UNDER the authoritative pack (pin matches) builds normally."""
    authoritative = _authoritative_pack()
    monkeypatch.setattr(loader_mod, "load_pack", lambda _j: authoritative)
    matter.rule_pack_version = authoritative.version
    matter.rule_pack_fingerprint = authoritative.fingerprint
    db.commit()
    draft = _happy_matter(db, storage, matter, attorney)
    result = build_mod.build_artifact_set(
        db, storage, matter=matter, draft=draft, user=attorney, firm_name="F"
    )
    assert result.reused is False
    assert db.scalars(select(ArtifactSet)).one() is not None


def test_prod_env_enforces_even_with_false_override_and_no_lifespan(
    db: Session,
    storage: LocalDiskStorage,
    pinned_matter: Matter,
    attorney: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct caller under APP_ENV=prod cannot disable the gate: the domain guard keys on
    the environment OR the setting, and no lifespan validation needs to have run."""
    draft = _happy_matter(db, storage, pinned_matter, attorney)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("REQUIRE_AUDITED_RULE_PACK_FOR_PACKAGE", "false")  # injected override
    get_settings.cache_clear()
    try:
        with pytest.raises(RulePackUnaudited):
            build_mod.build_artifact_set(
                db, storage, matter=pinned_matter, draft=draft, user=attorney, firm_name="F"
            )
    finally:
        get_settings.cache_clear()
    _assert_no_side_effects(db, storage)
