"""The ``ArtifactSet`` orchestration — build + store the four demand-package artifacts.

The single entry point that turns an approved draft into a persisted, immutable
:class:`~app.models.orm.ArtifactSet`: it composes the pure builders
(:mod:`app.package.artifacts`, :mod:`app.package.binder`, :mod:`app.package.provenance`) over the
M4 manifest (:func:`app.package.manifest.build_draft_manifest`) and the chronology
(:func:`app.engine.brain1.chronology.build_chronology`), stores each artifact through the object
door, and records the set + an audit event.

Contract boundaries this holds:

* **Immutable, versioned (package_builder §3).** A set for ``(matter, draft.version,
  draft.registry_version)`` is built once — a re-request returns the existing set
  (``reused=True``); a rebuild after drift is a NEW set under new versions, never an overwrite.
* **Atomic.** All four artifacts build (and the binder gate passes) BEFORE any row is written; a
  :class:`~app.package.binder.BinderBlocked` / integrity failure propagates with nothing persisted.
* **No gate transitions.** This builds + stores; advancing the gate on ``artifacts_built`` is the
  wiring wave's job (04 §2). This function only writes the ``artifact_set_built`` audit event.

Determinism (inv 10): the artifact *bytes* are pinned (see the builders); the ``ArtifactSet`` row's
``created_at`` is a wall-clock DB default and is deliberately NOT part of the bytes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.storage import ObjectStorage
from app.core.tenancy import tenant_add
from app.engine.brain1.chronology import build_chronology, render_rows_for_wire
from app.models.enums import ArtifactKind, SectionValidation
from app.models.orm import ArtifactSet, DemandDraft, DraftSection, Matter, RiskFlag, User
from app.package import artifacts as artifacts_mod
from app.package import binder as binder_mod
from app.package import provenance as provenance_mod
from app.package.manifest import build_draft_manifest


@dataclass(frozen=True)
class BuildResult:
    """The outcome of :func:`build_artifact_set` — the set plus whether it was reused.

    ``reused=True`` means an identical-versioned set already existed and was returned untouched
    (immutable build); ``False`` means this call built + stored the artifacts and wrote the row.
    """

    artifact_set: ArtifactSet
    reused: bool


def _passed_sections(db: Session, *, draft: DemandDraft) -> list[DraftSection]:
    """The draft's PASSED sections, ordered ``(sort_order, section_id)`` — the letter's sections.

    Only ``PASSED`` sections ship: a ``retry_pending`` / ``surfaced_failed`` section did not clear
    deterministic validation (brain2 inv 1/5), so it is not in the deliverable letter. The stable
    ``(sort_order, section_id)`` order matches the letter collation order.
    """
    rows = list(
        db.scalars(
            select(DraftSection).where(
                DraftSection.draft_id == draft.id,
                DraftSection.validation == SectionValidation.PASSED.value,
            )
        )
    )
    return sorted(rows, key=lambda s: (s.sort_order, s.section_id))


def _matter_flags(db: Session, *, matter: Matter) -> list[RiskFlag]:
    """All of the matter's risk flags (the provenance report's adverse-facts trail input)."""
    return list(db.scalars(select(RiskFlag).where(RiskFlag.matter_id == matter.id)))


def _sha256(data: bytes) -> str:
    """The hex sha256 of ``data`` (the artifact-content digest recorded on the set)."""
    return hashlib.sha256(data).hexdigest()


def _existing_set(db: Session, *, matter: Matter, draft: DemandDraft) -> ArtifactSet | None:
    """The built set for ``(matter, draft.version, draft.registry_version)``, or ``None``."""
    return db.scalar(
        select(ArtifactSet).where(
            ArtifactSet.matter_id == matter.id,
            ArtifactSet.draft_version == draft.version,
            ArtifactSet.registry_version == draft.registry_version,
        )
    )


def _artifact_key(*, matter: Matter, draft: DemandDraft, filename: str) -> str:
    """The storage key for one artifact under the matter's versioned artifacts prefix."""
    return f"matters/{matter.id}/artifacts/v{draft.version}.{draft.registry_version}/{filename}"


def build_artifact_set(
    db: Session,
    storage: ObjectStorage,
    *,
    matter: Matter,
    draft: DemandDraft,
    user: User,
    firm_name: str,
) -> BuildResult:
    """Build (or reuse) the immutable :class:`ArtifactSet` for an approved draft.

    Flow:

    1. **Reuse.** An existing set for ``(matter, draft.version, draft.registry_version)`` -> return
       it with ``reused=True`` (immutable; a rebuild after drift is a new version, not an
       overwrite).
    2. **Manifest.** :func:`build_draft_manifest` with ``mint_tokens=True`` (the manifest carries
       the EX tokens the binder index / bookmarks show).
    3. **Build all four artifacts** (nothing persisted yet): the binder (a
       :class:`~app.package.binder.BinderBlocked` / integrity failure propagates here, before any
       write — atomicity); the letter over the PASSED sections; the chronology xlsx over
       ``build_chronology(generate_narratives=False)`` rendered rows; the provenance report.
    4. **Store + record.** ``storage.put`` each under the versioned key; compute sha256 + byte
       count; write the :class:`ArtifactSet` row (``artifacts`` JSON keyed by
       :class:`~app.models.enums.ArtifactKind` values) + an ``artifact_set_built`` audit event;
       commit.

    Does NOT transition the gate (the wiring wave owns the ``artifacts_built`` advance). Returns the
    persisted set with ``reused=False``.
    """
    existing = _existing_set(db, matter=matter, draft=draft)
    if existing is not None:
        return BuildResult(artifact_set=existing, reused=True)

    settings = get_settings()

    # -- 2. Manifest (mint EX tokens so the binder index + bookmarks resolve). --
    manifest = build_draft_manifest(db, matter=matter, mint_tokens=True)

    # -- 3. Build all four artifacts BEFORE any persistence (atomicity). --
    # Binder first: its build-time gate (BinderBlocked) / integrity checks (BinderPageMissing) must
    # fail the whole build with nothing written.
    binder_bytes, _bates_by_document = binder_mod.build_binder_pdf(
        db,
        storage,
        matter=matter,
        manifest=manifest,
        bates_prefix=settings.bates_prefix,
    )

    sections = _passed_sections(db, draft=draft)
    letter_bytes = artifacts_mod.build_letter_docx(
        firm_name=firm_name,
        client_display_name=matter.client_display_name,
        sections=sections,
        memo=draft.memo,  # accepted but excluded from the carrier letter (v1)
    )

    chronology_outcome = build_chronology(db, None, matter=matter, generate_narratives=False)
    chronology_rows = render_rows_for_wire(db, matter=matter, rows=chronology_outcome.rows)
    xlsx_bytes = artifacts_mod.build_chronology_xlsx(chronology_rows)

    provenance_bytes = provenance_mod.build_provenance_report(
        db,
        matter=matter,
        draft=draft,
        sections=sections,
        flags=_matter_flags(db, matter=matter),
    )

    # -- 4. Store + record. (kind, filename, bytes) tuples in a fixed order. --
    built: Sequence[tuple[ArtifactKind, str, bytes]] = (
        (ArtifactKind.LETTER_DOCX, "letter.docx", letter_bytes),
        (ArtifactKind.BINDER_PDF, "binder.pdf", binder_bytes),
        (ArtifactKind.CHRONOLOGY_XLSX, "chronology.xlsx", xlsx_bytes),
        (ArtifactKind.PROVENANCE_REPORT, "provenance_report.pdf", provenance_bytes),
    )

    artifacts_manifest: list[dict] = []
    for kind, filename, data in built:
        key = _artifact_key(matter=matter, draft=draft, filename=filename)
        storage.put(key, data)
        artifacts_manifest.append(
            {
                "kind": kind.value,
                "object_key": key,
                "sha256": _sha256(data),
                "byte_count": len(data),
            }
        )

    artifact_set = ArtifactSet(
        matter_id=matter.id,
        draft_id=draft.id,
        draft_version=draft.version,
        registry_version=draft.registry_version,
        artifacts=artifacts_manifest,
        built_by=user.id,
    )
    tenant_add(db, artifact_set, matter.firm_id)

    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind="artifact_set_built",
        payload={
            "matter_id": str(matter.id),
            "draft_id": str(draft.id),
            "draft_version": draft.version,
            "registry_version": draft.registry_version,
            "kinds": [kind.value for kind, _, _ in built],
        },
    )
    db.commit()
    return BuildResult(artifact_set=artifact_set, reused=False)


__all__ = ["BuildResult", "build_artifact_set"]
