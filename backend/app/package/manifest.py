"""The draft binder manifest read-model + exhibit picks + EX-token minting (package_builder §3).

M4 Wave B2 builds the *preview* of the M5 exhibit binder: a paralegal/attorney picks pages per
source document (:func:`upsert_exhibit_pick`), an attorney dispositions third-party PHI
(:func:`set_phi_disposition`), and :func:`build_draft_manifest` assembles the ordered, integrity-
checked manifest — with an optional pass that mints the ``[[EX_n]]`` tokens through the registry
(the only minter). The M5 build gate reads this manifest's ``blocking`` list; nothing here builds a
PDF.

Design pins carried here:

* **Tri-state pages.** ``include_pages`` collate; ``excluded_pages`` are explicitly dropped; a page
  in neither is "not yet decided". Both lists are sorted + deduped on write.
* **PHI is dispositioned explicitly.** ``phi_disposition`` defaults ``pending`` and transitions
  only through :func:`set_phi_disposition` (attorney-only). An undispositioned ``third_party_phi``
  risk flag on the document forces/keeps ``pending`` on pick, but resolving the *flag* does NOT
  auto-clear the exhibit — defense in depth: the M5 build gate is a separate check (contract note
  lands in the E wave).
* **Integrity is per-entry, blocking is matter-level.** An entry is ``ok`` unless its include list
  is empty, a page falls outside ``1..page_count``, or its document was dedup-superseded. The
  manifest ``blocking`` list is the human-readable M5 gate preview: any pending PHI on an entry
  that has includes, plus every non-``ok`` integrity verdict.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.tenancy import tenant_add
from app.engine.tokenizer import registry
from app.models.enums import (
    DedupResolution,
    FlagKind,
    PhiDisposition,
    UserRole,
)
from app.models.orm import (
    CaseDocument,
    DedupDecision,
    Exhibit,
    FactToken,
    Matter,
    RiskFlag,
    User,
)
from app.models.schemas import ExhibitPickRequest

# Integrity verdicts for a manifest entry (the M5 build gate reads these strings).
_INTEGRITY_OK = "ok"
_INTEGRITY_EMPTY_INCLUDE = "empty_include"
_INTEGRITY_PAGE_OUT_OF_RANGE = "page_out_of_range"
_INTEGRITY_DOC_SUPERSEDED = "doc_superseded"


class InvalidPick(Exception):
    """A rejected exhibit pick (the route maps this to a 422 ``invalid_pick``).

    Carries a stable ``reason`` code and a human ``detail`` (naming the offending pages / document)
    so the refusal is specific. Raised before any row is written — a bad pick never half-applies.
    """

    def __init__(self, *, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"invalid exhibit pick ({reason}): {detail}")


class PhiDispositionForbidden(Exception):
    """A non-attorney attempted to change an exhibit's PHI disposition (route maps to 403).

    Any PHI-disposition change is an attorney judgment (someone else's medical data is being
    cleared into or dropped from the binder); a paralegal preps picks but does not make this call.
    """

    def __init__(self, *, actual_role: str) -> None:
        self.actual_role = actual_role
        super().__init__(f"phi disposition is attorney-only; actor role is {actual_role!r}")


@dataclass(frozen=True)
class ManifestEntry:
    """One document's slot in the draft binder manifest (a read-model, not persisted)."""

    exhibit_token: str | None  # "[[EX_n]]" once minted; None pre-mint
    document_id: uuid.UUID
    filename: str
    included_pages: tuple[int, ...]
    excluded_pages: tuple[int, ...]
    phi_disposition: str
    sort_order: int
    page_count: int  # the document's total page count
    integrity: str  # one of the _INTEGRITY_* verdicts


@dataclass(frozen=True)
class DraftBinderManifest:
    """The ordered, integrity-checked draft binder manifest for a matter (the M4-exit read)."""

    matter_id: uuid.UUID
    entries: tuple[ManifestEntry, ...]
    blocking: tuple[str, ...]  # human-readable M5-build-gate blockers


def _sorted_unique(pages: list[int]) -> tuple[int, ...]:
    """Sort + dedupe a page list (schema already guarantees each is ``>= 1``)."""
    return tuple(sorted(set(pages)))


def _has_open_third_party_phi(db: Session, *, matter: Matter, document_id: uuid.UUID) -> bool:
    """Whether the document has an UNDISPOSITIONED ``third_party_phi`` risk flag.

    An undispositioned flag (``disposition IS NULL``) means the third-party-PHI question is still
    open for this document, so a fresh/updated pick must sit at ``pending`` until an attorney
    dispositions the exhibit. Anchors carry ``document_id``; a flag anchored to this doc counts.
    """
    flags = list(
        db.scalars(
            select(RiskFlag).where(
                RiskFlag.matter_id == matter.id,
                RiskFlag.kind == FlagKind.THIRD_PARTY_PHI.value,
                RiskFlag.disposition.is_(None),
            )
        )
    )
    for flag in flags:
        anchors = flag.anchors if isinstance(flag.anchors, list) else []
        for anchor in anchors:
            if isinstance(anchor, dict) and str(anchor.get("document_id")) == str(document_id):
                return True
    return False


def upsert_exhibit_pick(
    db: Session, *, user: User, matter: Matter, pick: ExhibitPickRequest
) -> Exhibit:
    """Create-or-update the exhibit pick for ``(matter, document)``; return the row.

    Validation (all typed :class:`InvalidPick`, raised before any write):

    * the document must belong to the matter (else ``document_not_in_matter``);
    * ``include_pages`` and ``excluded_pages`` must be disjoint (else ``include_exclude_overlap``,
      naming the overlap);
    * every page in either list must be within ``1..document.page_count`` (else
      ``page_out_of_range``, naming the offenders).

    Pages are sorted + deduped. If the document has an undispositioned ``third_party_phi`` flag the
    disposition stays/becomes ``pending`` (the flag drives it here; the M5 gate is separate). Writes
    an ``exhibit_pick_upserted`` audit event and commits.
    """
    document = db.get(CaseDocument, pick.document_id)
    if document is None or document.matter_id != matter.id:
        raise InvalidPick(
            reason="document_not_in_matter",
            detail=f"document {pick.document_id} is not part of matter {matter.id}",
        )

    include = _sorted_unique(list(pick.include_pages))
    exclude = _sorted_unique(list(pick.excluded_pages))

    overlap = sorted(set(include) & set(exclude))
    if overlap:
        raise InvalidPick(
            reason="include_exclude_overlap",
            detail=f"pages appear in both include and exclude: {overlap}",
        )

    page_count = document.page_count
    out_of_range = sorted(p for p in (*include, *exclude) if p < 1 or p > page_count)
    if out_of_range:
        raise InvalidPick(
            reason="page_out_of_range",
            detail=f"pages outside 1..{page_count} for document {pick.document_id}: {out_of_range}",
        )

    exhibit = db.execute(
        select(Exhibit).where(
            Exhibit.matter_id == matter.id,
            Exhibit.document_id == pick.document_id,
        )
    ).scalar_one_or_none()

    created = exhibit is None
    if exhibit is None:
        exhibit = Exhibit(
            matter_id=matter.id,
            document_id=pick.document_id,
            include_pages=list(include),
            excluded_pages=list(exclude),
            sort_order=pick.sort_order,
        )
        tenant_add(db, exhibit, matter.firm_id)
    else:
        exhibit.include_pages = list(include)  # reassign: JSON change detection
        exhibit.excluded_pages = list(exclude)
        exhibit.sort_order = pick.sort_order

    # An open third-party-PHI flag forces pending; it never auto-clears here (defense in depth —
    # the M5 build gate is a separate check, and clearing is an explicit attorney disposition).
    if _has_open_third_party_phi(db, matter=matter, document_id=pick.document_id):
        exhibit.phi_disposition = PhiDisposition.PENDING.value

    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind="exhibit_pick_upserted",
        payload={
            "matter_id": str(matter.id),
            "document_id": str(pick.document_id),
            "created": created,
            "include_pages": list(include),
            "excluded_pages": list(exclude),
            "sort_order": pick.sort_order,
        },
    )
    db.commit()
    return exhibit


def set_phi_disposition(
    db: Session, *, user: User, exhibit: Exhibit, disposition: PhiDisposition
) -> Exhibit:
    """Set an exhibit's third-party-PHI disposition — **attorney-only**; return the row.

    Any change (including re-affirming) is an attorney judgment: a non-attorney raises
    :class:`PhiDispositionForbidden` (route → 403). Writes a ``phi_disposition_set`` audit event
    (old + new) and commits.
    """
    if user.role != UserRole.ATTORNEY.value:
        raise PhiDispositionForbidden(actual_role=user.role)

    old = exhibit.phi_disposition
    exhibit.phi_disposition = disposition.value
    record_event(
        db,
        firm_id=exhibit.firm_id,
        actor_id=user.id,
        event_kind="phi_disposition_set",
        payload={
            "exhibit_id": str(exhibit.id),
            "document_id": str(exhibit.document_id),
            "old_disposition": old,
            "new_disposition": disposition.value,
        },
    )
    db.commit()
    return exhibit


def _superseded_document_ids(db: Session, *, matter: Matter) -> set[uuid.UUID]:
    """Document ids dropped by a dedup-superseded decision (drives ``doc_superseded``)."""
    return set(
        db.scalars(
            select(DedupDecision.document_id).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.resolution == DedupResolution.SUPERSEDED.value,
            )
        )
    )


def _integrity_for(*, include: tuple[int, ...], page_count: int, superseded: bool) -> str:
    """The integrity verdict for one entry (order: superseded > empty include > out-of-range).

    A superseded document is the strongest signal (the whole document dropped out of the case), so
    it wins; then an empty include list (nothing to collate); then any included page beyond the
    document's page range. Otherwise ``ok``.
    """
    if superseded:
        return _INTEGRITY_DOC_SUPERSEDED
    if not include:
        return _INTEGRITY_EMPTY_INCLUDE
    if any(p < 1 or p > page_count for p in include):
        return _INTEGRITY_PAGE_OUT_OF_RANGE
    return _INTEGRITY_OK


def build_draft_manifest(
    db: Session, *, matter: Matter, mint_tokens: bool = False
) -> DraftBinderManifest:
    """Assemble the matter's draft binder manifest, ordered ``(sort_order, filename)``.

    Each :class:`Exhibit` becomes a :class:`ManifestEntry` with a per-entry integrity verdict
    (:func:`_integrity_for`). ``blocking`` collects the human-readable M5-build-gate blockers: a
    ``pending`` PHI disposition on any entry that HAS includes (an entry with nothing to collate
    isn't blocked by PHI), plus every non-``ok`` integrity verdict.

    ``mint_tokens=True`` mints the ``[[EX_n]]`` tokens through the registry
    (:func:`app.engine.tokenizer.registry.mint_exhibits`) for entries whose integrity is ``ok``,
    keyed by ``exhibit:<document_id>`` with ``display_form = "Exhibit {ordinal} — {filename}"`` (the
    1-based ordinal over the ok entries in manifest order) and anchors = the included pages as
    ``PageAnchor`` dicts. The freshly-minted token strings are then stamped onto the returned
    entries. Minting is idempotent — a second call with the same picks mints nothing new.
    """
    exhibits = list(db.scalars(select(Exhibit).where(Exhibit.matter_id == matter.id)))
    # A document's filename + page_count for each exhibit (one query per matter's docs).
    doc_ids = [ex.document_id for ex in exhibits]
    docs_by_id: dict[uuid.UUID, CaseDocument] = {}
    if doc_ids:
        docs_by_id = {
            doc.id: doc
            for doc in db.scalars(select(CaseDocument).where(CaseDocument.id.in_(doc_ids)))
        }
    superseded = _superseded_document_ids(db, matter=matter)

    # Order is the manifest collation order: (sort_order, filename) with document_id as a stable
    # final tiebreak so the ordering is total even when two exhibits share sort_order + filename.
    def _order_key(ex: Exhibit) -> tuple[int, str, str]:
        doc = docs_by_id.get(ex.document_id)
        filename = doc.filename if doc is not None else ""
        return (ex.sort_order, filename, str(ex.document_id))

    ordered = sorted(exhibits, key=_order_key)

    entries: list[ManifestEntry] = []
    blocking: list[str] = []
    for ex in ordered:
        doc = docs_by_id.get(ex.document_id)
        filename = doc.filename if doc is not None else ""
        page_count = doc.page_count if doc is not None else 0
        include = tuple(ex.include_pages) if isinstance(ex.include_pages, list) else ()
        exclude = tuple(ex.excluded_pages) if isinstance(ex.excluded_pages, list) else ()
        integrity = _integrity_for(
            include=include,
            page_count=page_count,
            superseded=ex.document_id in superseded,
        )
        entries.append(
            ManifestEntry(
                exhibit_token=None,
                document_id=ex.document_id,
                filename=filename,
                included_pages=include,
                excluded_pages=exclude,
                phi_disposition=ex.phi_disposition,
                sort_order=ex.sort_order,
                page_count=page_count,
                integrity=integrity,
            )
        )

        if integrity != _INTEGRITY_OK:
            blocking.append(f"integrity {integrity}: {filename or ex.document_id}")
        # PHI only blocks an entry that actually has pages to collate.
        if include and ex.phi_disposition == PhiDisposition.PENDING.value:
            blocking.append(f"pending PHI disposition: {filename or ex.document_id}")

    if mint_tokens:
        entries = _mint_and_stamp(db, matter=matter, entries=entries)

    return DraftBinderManifest(
        matter_id=matter.id,
        entries=tuple(entries),
        blocking=tuple(blocking),
    )


def _mint_and_stamp(
    db: Session, *, matter: Matter, entries: list[ManifestEntry]
) -> list[ManifestEntry]:
    """Mint EX tokens for the ok entries (in manifest order) and stamp the token onto each.

    Ordinals in the display form are 1-based over the ok entries in manifest order. The registry
    keys each token ``exhibit:<document_id>`` (idempotent), so re-minting the same picks is a no-op;
    the returned entries carry the resulting ``[[EX_n]]`` token string.
    """
    ok_entries = [e for e in entries if e.integrity == _INTEGRITY_OK]
    if not ok_entries:
        return entries

    mint_specs: list[dict[str, object]] = []
    for ordinal, entry in enumerate(ok_entries, start=1):
        mint_specs.append(
            {
                "key": str(entry.document_id),
                "display_form": f"Exhibit {ordinal} — {entry.filename}",
                "anchors": [
                    {"document_id": str(entry.document_id), "page": page}
                    for page in entry.included_pages
                ],
            }
        )
    registry.mint_exhibits(db, matter=matter, entries=mint_specs)

    # Read back the minted token string per document_id (source_ref exhibit:<document_id>).
    token_by_doc: dict[str, str] = {}
    for spec in mint_specs:
        key = str(spec["key"])
        token = _latest_exhibit_token(db, matter=matter, document_id=key)
        if token is not None:
            token_by_doc[key] = token

    stamped: list[ManifestEntry] = []
    for entry in entries:
        token = token_by_doc.get(str(entry.document_id))
        stamped.append(
            ManifestEntry(
                exhibit_token=token if token is not None else entry.exhibit_token,
                document_id=entry.document_id,
                filename=entry.filename,
                included_pages=entry.included_pages,
                excluded_pages=entry.excluded_pages,
                phi_disposition=entry.phi_disposition,
                sort_order=entry.sort_order,
                page_count=entry.page_count,
                integrity=entry.integrity,
            )
        )
    return stamped


def _latest_exhibit_token(db: Session, *, matter: Matter, document_id: str) -> str | None:
    """The full token string (e.g. ``[[EX_3]]``) for ``document_id``'s exhibit, or ``None``.

    Looks the token up by its deterministic ``source_ref`` (``exhibit:<document_id>``), taking the
    latest-version row for its slot and wrapping the bare ``token_id`` back into the canonical
    ``[[..]]`` form via the registry grammar.
    """
    rows = list(
        db.scalars(
            select(FactToken).where(
                FactToken.matter_id == matter.id,
                FactToken.source_ref == f"exhibit:{document_id}",
            )
        )
    )
    if not rows:
        return None
    latest = max(rows, key=lambda r: r.registry_version)
    kind, ordinal = registry.parse_token(f"[[{latest.token_id}]]")
    return registry.token_str(kind, ordinal)


__all__ = [
    "DraftBinderManifest",
    "InvalidPick",
    "ManifestEntry",
    "PhiDispositionForbidden",
    "build_draft_manifest",
    "set_phi_disposition",
    "upsert_exhibit_pick",
]
