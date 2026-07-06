"""Two-stage document dedup (component corpus_ingest §4 A6).

Stage 1 finds exact page-hash collisions; stage 2 finds shingled fuzzy overlap on normalized
page text. A verdict is NEVER auto-merged — it is quarantined as a :class:`DedupDecision` row
with ``resolution=pending`` that a human resolves (kept vs superseded) in the Document Center.
This is the double-counted-specials lesson made structural: the money engine must never silently
drop a "duplicate" the software guessed at.

Image-only documents (no non-empty page text) are undedupable at M1 — there is no text layer to
hash or shingle — so they stay ``unique``.

Only *strictly earlier* documents in the same matter are candidates: a document is compared
against those already present when it arrived, never against a later arrival. ``(created_at, id)``
tuples order documents deterministically even when SQLite timestamps tie.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.tenancy import tenant_add
from app.models.enums import DedupResolution, DedupStatus, DocStatus
from app.models.orm import CaseDocument, DedupDecision, DocumentPage, User

# --------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------


def normalize_page_text(text: str) -> str:
    """Casefold and collapse all whitespace runs to single spaces.

    The canonical form both hashing and shingling operate on, so trivial whitespace/case
    differences between two scans of the same page do not defeat the exact-match stage.
    """
    return " ".join(text.casefold().split())


def page_hash(text: str) -> str:
    """The SHA-256 hex digest of :func:`normalize_page_text` — the exact-match key for a page."""
    return hashlib.sha256(normalize_page_text(text).encode("utf-8")).hexdigest()


def shingles(text: str, k: int) -> set[str]:
    """Word ``k``-grams over the normalized text — the fuzzy-overlap feature set.

    When the text has fewer than ``k`` words, the whole (normalized) text is a single shingle so
    short pages still compare meaningfully; empty text yields the empty set.
    """
    words = normalize_page_text(text).split()
    if not words:
        return set()
    if len(words) < k:
        return {" ".join(words)}
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two shingle sets; ``0.0`` when both are empty (no evidence)."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# --------------------------------------------------------------------------------------
# Dedup run
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DedupOutcome:
    """The verdict of a dedup run for one new document.

    ``status`` is the :class:`~app.models.enums.DedupStatus` value written to the document.
    ``decision_id`` is the quarantined :class:`DedupDecision` row (``None`` for ``unique``).
    ``undedupable`` marks an all-empty new document (image-only), which stays ``unique`` with no
    decision row.
    """

    status: str
    against_document_id: uuid.UUID | None
    decision_id: uuid.UUID | None
    undedupable: bool


class DedupAlreadyResolved(Exception):
    """Raised when resolving a decision that is not ``pending`` — a verdict is resolved once."""


def _load_candidates(db: Session, document: CaseDocument) -> list[CaseDocument]:
    """Other docs in the matter strictly BEFORE this one by ``(created_at, id)``, not FAILED, >0p.

    Ordering by the ``(created_at, id)`` tuple is deterministic even when SQLite timestamps tie:
    the id breaks the tie, so "strictly earlier" is a total order.
    """
    others = db.scalars(
        select(CaseDocument).where(
            CaseDocument.matter_id == document.matter_id,
            CaseDocument.id != document.id,
            CaseDocument.status != DocStatus.FAILED.value,
            CaseDocument.page_count > 0,
        )
    )
    this_key = (document.created_at, document.id)
    earlier = [c for c in others if (c.created_at, c.id) < this_key]
    earlier.sort(key=lambda c: (c.created_at, c.id))
    return earlier


def _ordered_pages(db: Session, document_id: uuid.UUID) -> list[DocumentPage]:
    """This document's pages in ascending ``page_no`` order (deterministic pairing)."""
    return list(
        db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_no)
        )
    )


def _exact_match_pairs(
    new_pages: list[DocumentPage], cand_pages: list[DocumentPage]
) -> list[list[int]]:
    """Pair each non-empty new page with the FIRST candidate page whose hash matches it.

    Ascending candidate page order makes the pairing deterministic. Returns ``[[new_no, cand_no],
    ...]``; a new page with no hash match contributes nothing.
    """
    cand_hashes = [
        (p.page_no, page_hash(p.text)) for p in cand_pages if normalize_page_text(p.text)
    ]
    pairs: list[list[int]] = []
    for np in new_pages:
        if not normalize_page_text(np.text):
            continue
        nh = page_hash(np.text)
        for cand_no, ch in cand_hashes:
            if ch == nh:
                pairs.append([np.page_no, cand_no])
                break
    return pairs


def _doc_shingles(pages: list[DocumentPage], k: int) -> set[str]:
    """The union of ``k``-shingles over a document's non-empty page texts."""
    out: set[str] = set()
    for p in pages:
        if normalize_page_text(p.text):
            out |= shingles(p.text, k)
    return out


def run_dedup(db: Session, *, document: CaseDocument) -> DedupOutcome:
    """Run two-stage dedup for ``document`` against strictly-earlier docs in the same matter.

    Never mutates a candidate document. On a DUPLICATE_OF / PARTIAL_OVERLAP verdict, creates a
    quarantined :class:`DedupDecision` (``resolution=pending``) and sets ``document.dedup_status``;
    otherwise sets ``unique`` with no decision row. An all-empty (image-only) new document is
    ``unique`` + ``undedupable`` with no decision row.
    """
    new_pages = _ordered_pages(db, document.id)
    new_nonempty = [p for p in new_pages if normalize_page_text(p.text)]

    if not new_nonempty:
        # Image-only / no text layer: nothing to hash or shingle at M1.
        document.dedup_status = DedupStatus.UNIQUE.value
        db.commit()
        return DedupOutcome(DedupStatus.UNIQUE.value, None, None, undedupable=True)

    candidates = _load_candidates(db, document)
    cand_pages: dict[uuid.UUID, list[DocumentPage]] = {
        c.id: _ordered_pages(db, c.id) for c in candidates
    }

    # Stage 1 — exact page-hash collisions. A candidate that matches EVERY non-empty new page is a
    # full duplicate; the earliest such candidate wins (candidates are pre-sorted earliest-first).
    for cand in candidates:
        pairs = _exact_match_pairs(new_pages, cand_pages[cand.id])
        matched_new_pages = {pair[0] for pair in pairs}
        if matched_new_pages == {p.page_no for p in new_nonempty}:
            return _quarantine(
                db,
                document=document,
                status=DedupStatus.DUPLICATE_OF,
                against=cand,
                page_hash_matches=pairs,
                shingle_overlap=None,
            )

    # Stage 2 — shingled fuzzy overlap (only reached when no candidate fully matches). Best
    # doc-level Jaccard at/above the threshold is a partial overlap; record that candidate's exact
    # page pairs (possibly empty) alongside the score.
    k = get_settings().shingle_size
    threshold = get_settings().shingle_overlap_threshold
    new_shingles = _doc_shingles(new_pages, k)
    best_cand: CaseDocument | None = None
    best_score = 0.0
    for cand in candidates:
        score = jaccard(new_shingles, _doc_shingles(cand_pages[cand.id], k))
        if score > best_score:
            best_score = score
            best_cand = cand
    if best_cand is not None and best_score >= threshold:
        return _quarantine(
            db,
            document=document,
            status=DedupStatus.PARTIAL_OVERLAP,
            against=best_cand,
            page_hash_matches=_exact_match_pairs(new_pages, cand_pages[best_cand.id]),
            shingle_overlap=best_score,
        )

    document.dedup_status = DedupStatus.UNIQUE.value
    db.commit()
    return DedupOutcome(DedupStatus.UNIQUE.value, None, None, undedupable=False)


def _quarantine(
    db: Session,
    *,
    document: CaseDocument,
    status: DedupStatus,
    against: CaseDocument,
    page_hash_matches: list[list[int]],
    shingle_overlap: float | None,
) -> DedupOutcome:
    """Create the quarantined :class:`DedupDecision` and set the new doc's status. Never touches
    the candidate document — dedup only flags, it never merges."""
    decision = DedupDecision(
        matter_id=document.matter_id,
        document_id=document.id,
        against_document_id=against.id,
        status=status.value,
        page_hash_matches=page_hash_matches,
        shingle_overlap=shingle_overlap,
        resolution=DedupResolution.PENDING.value,
    )
    tenant_add(db, decision, document.firm_id)
    document.dedup_status = status.value
    db.flush()  # assign decision.id for the outcome
    decision_id = decision.id
    db.commit()
    return DedupOutcome(status.value, against.id, decision_id, undedupable=False)


def resolve_dedup_decision(
    db: Session, *, user: User, decision: DedupDecision, resolution: DedupResolution
) -> DedupDecision:
    """Record a human resolution (``kept`` / ``superseded``) on a pending dedup decision.

    Refuses (:class:`DedupAlreadyResolved`) unless ``resolution`` is a real disposition and the
    decision is still ``pending`` — a verdict is resolved exactly once. Stamps ``resolved_by`` and
    a naive-UTC ``resolved_at`` (SQLite stores naive datetimes, so we drop the tzinfo) and writes a
    ``dedup_resolved`` audit event.

    At M1 ``superseded`` is a RECORDED verdict only; the money engine consumes it at M2 to drop the
    superseded document's billing lines from the specials ledger.
    """
    if resolution not in (DedupResolution.KEPT, DedupResolution.SUPERSEDED):
        raise DedupAlreadyResolved(f"{resolution!r} is not a resolving disposition")
    if decision.resolution != DedupResolution.PENDING.value:
        raise DedupAlreadyResolved(
            f"decision {decision.id} already resolved as {decision.resolution!r}"
        )

    decision.resolution = resolution.value
    decision.resolved_by = user.id
    # SQLite convention: store a naive UTC timestamp (no tzinfo) to match the DateTime column.
    decision.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    record_event(
        db,
        firm_id=decision.firm_id,
        actor_id=user.id,
        event_kind="dedup_resolved",
        payload={
            "decision_id": str(decision.id),
            "document_id": str(decision.document_id),
            "resolution": resolution.value,
        },
    )
    db.commit()
    return decision
