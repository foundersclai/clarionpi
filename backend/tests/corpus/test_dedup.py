"""Two-stage dedup: pure hashing/shingle helpers, exact + fuzzy verdicts, quarantine, resolve.

The load-bearing invariant proven throughout: dedup NEVER auto-merges. A duplicate/overlap
becomes a quarantined ``DedupDecision`` (``resolution=pending``) that a human resolves; the
candidate document is never mutated. Documents and pages are built directly via the ORM (this
suite does not drive the page pipeline).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.core.tenancy import tenant_add
from app.corpus.ingest.dedup import (
    DedupAlreadyResolved,
    jaccard,
    normalize_page_text,
    page_hash,
    resolve_dedup_decision,
    run_dedup,
    shingles,
)
from app.models.enums import DedupResolution, DedupStatus, DocStatus, DocType
from app.models.orm import AuditEvent, CaseDocument, DedupDecision, DocumentPage, Matter, User

_BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _mk_doc(
    db: Session,
    matter: Matter,
    *,
    pages: list[str],
    status: DocStatus = DocStatus.CLASSIFIED,
    created_at: dt.datetime | None = None,
    dedup_status: DedupStatus = DedupStatus.UNIQUE,
) -> CaseDocument:
    """Insert a CaseDocument + its DocumentPages directly, with an explicit ``created_at`` so
    candidate ordering is deterministic (no sleeps)."""
    doc = CaseDocument(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.MEDICAL_RECORD.value,
        source_label="d",
        filename="d.pdf",
        storage_key="k/d.pdf",
        page_count=len(pages),
        dedup_status=dedup_status.value,
        status=status.value,
    )
    if created_at is not None:
        doc.created_at = created_at
    tenant_add(db, doc, matter.firm_id)
    db.flush()
    for i, text in enumerate(pages, start=1):
        db.add(
            DocumentPage(
                firm_id=matter.firm_id,
                document_id=doc.id,
                page_no=i,
                text=text,
                text_source="text_layer",
                zero_text=(text == ""),
            )
        )
    db.commit()
    return doc


# --------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------


def test_normalize_page_text_casefolds_and_collapses_whitespace() -> None:
    assert normalize_page_text("  Hello\t WORLD\n\n foo ") == "hello world foo"
    assert normalize_page_text("") == ""
    assert normalize_page_text("   ") == ""


def test_page_hash_is_stable_and_normalization_insensitive() -> None:
    # Same text under different case/spacing hashes identically (that is the point of exact-match).
    assert page_hash("Hello World") == page_hash("  hello   world ")
    assert page_hash("a") != page_hash("b")
    # 64 hex chars — a sha256 digest.
    assert len(page_hash("x")) == 64


def test_shingles_kgram_edges() -> None:
    # Fewer words than k → the whole normalized text is one shingle.
    assert shingles("one two", 5) == {"one two"}
    # Empty → empty set.
    assert shingles("", 5) == set()
    assert shingles("   ", 3) == set()
    # Exactly k words → one shingle.
    assert shingles("a b c", 3) == {"a b c"}
    # More than k → sliding window.
    assert shingles("a b c d", 3) == {"a b c", "b c d"}


def test_jaccard_bounds() -> None:
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


# --------------------------------------------------------------------------------------
# run_dedup — exact, partial, unique, image-only, ordering, determinism
# --------------------------------------------------------------------------------------


def test_exact_duplicate_quarantines_pending_decision(
    db: Session, dev_user: User, matter: Matter
) -> None:
    texts = ["page one alpha", "page two beta", "page three gamma"]
    _mk_doc(db, matter, pages=texts, created_at=_BASE)
    new = _mk_doc(db, matter, pages=texts, created_at=_BASE + dt.timedelta(minutes=1))

    outcome = run_dedup(db, document=new)

    assert outcome.status == DedupStatus.DUPLICATE_OF.value
    assert outcome.undedupable is False
    assert outcome.decision_id is not None
    decision = db.get(DedupDecision, outcome.decision_id)
    assert decision is not None
    assert decision.resolution == DedupResolution.PENDING.value
    assert decision.page_hash_matches == [[1, 1], [2, 2], [3, 3]]
    assert decision.shingle_overlap is None  # exact match records no shingle score
    db.refresh(new)
    assert new.dedup_status == DedupStatus.DUPLICATE_OF.value


def test_exact_duplicate_leaves_candidate_untouched(
    db: Session, dev_user: User, matter: Matter
) -> None:
    texts = ["aaa bbb", "ccc ddd"]
    cand = _mk_doc(db, matter, pages=texts, created_at=_BASE)
    new = _mk_doc(db, matter, pages=texts, created_at=_BASE + dt.timedelta(minutes=1))

    run_dedup(db, document=new)

    db.refresh(cand)
    # The candidate is never mutated — dedup flags, it never merges.
    assert cand.dedup_status == DedupStatus.UNIQUE.value
    assert cand.status == DocStatus.CLASSIFIED.value
    # And the candidate gets no decision row of its own.
    cand_decisions = db.query(DedupDecision).filter(DedupDecision.document_id == cand.id).count()
    assert cand_decisions == 0


def test_partial_overlap_records_score(db: Session, dev_user: User, matter: Matter) -> None:
    # Candidate and new doc share 2 identical pages; the new doc adds 3 unrelated pages. The
    # shared pages give enough doc-level shingle overlap to cross the default 0.35 threshold while
    # NOT being a full duplicate (the extra pages have no match).
    shared = [
        "the quick brown fox jumps over the lazy dog near the river bank",
        "a second shared page with plenty of repeated common tokens here today",
    ]
    cand = _mk_doc(db, matter, pages=shared, created_at=_BASE)
    new = _mk_doc(
        db,
        matter,
        pages=[*shared, "xyz", "qrs", "lmn"],
        created_at=_BASE + dt.timedelta(minutes=1),
    )

    outcome = run_dedup(db, document=new)

    assert outcome.status == DedupStatus.PARTIAL_OVERLAP.value
    assert outcome.against_document_id == cand.id
    decision = db.get(DedupDecision, outcome.decision_id)
    assert decision is not None
    assert decision.shingle_overlap is not None
    assert decision.shingle_overlap >= 0.35
    # Stage-1 exact pairs for the 2 shared pages are still recorded alongside the fuzzy score.
    assert decision.page_hash_matches == [[1, 1], [2, 2]]
    db.refresh(new)
    assert new.dedup_status == DedupStatus.PARTIAL_OVERLAP.value


def test_distinct_documents_are_unique_no_decision(
    db: Session, dev_user: User, matter: Matter
) -> None:
    _mk_doc(db, matter, pages=["completely different content about apples"], created_at=_BASE)
    new = _mk_doc(
        db,
        matter,
        pages=["unrelated material concerning quantum orbital mechanics"],
        created_at=_BASE + dt.timedelta(minutes=1),
    )

    outcome = run_dedup(db, document=new)

    assert outcome.status == DedupStatus.UNIQUE.value
    assert outcome.decision_id is None
    assert outcome.undedupable is False
    assert db.query(DedupDecision).count() == 0
    db.refresh(new)
    assert new.dedup_status == DedupStatus.UNIQUE.value


def test_image_only_document_is_unique_and_undedupable(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # An earlier text doc exists, but the new doc has only empty (image-only) pages → undedupable.
    _mk_doc(db, matter, pages=["some real text here"], created_at=_BASE)
    new = _mk_doc(db, matter, pages=["", "", ""], created_at=_BASE + dt.timedelta(minutes=1))

    outcome = run_dedup(db, document=new)

    assert outcome.status == DedupStatus.UNIQUE.value
    assert outcome.undedupable is True
    assert outcome.decision_id is None
    assert db.query(DedupDecision).count() == 0


def test_only_the_later_document_gets_the_decision(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # A is processed first (no earlier candidate) → stays unique. B duplicates A → B gets flagged.
    texts = ["shared page x", "shared page y"]
    a = _mk_doc(db, matter, pages=texts, created_at=_BASE)
    b = _mk_doc(db, matter, pages=texts, created_at=_BASE + dt.timedelta(minutes=1))

    outcome_a = run_dedup(db, document=a)
    assert outcome_a.status == DedupStatus.UNIQUE.value
    assert outcome_a.decision_id is None

    outcome_b = run_dedup(db, document=b)
    assert outcome_b.status == DedupStatus.DUPLICATE_OF.value
    assert outcome_b.against_document_id == a.id
    # Exactly one decision row exists, and it belongs to B.
    decisions = db.query(DedupDecision).all()
    assert len(decisions) == 1
    assert decisions[0].document_id == b.id


def test_determinism_earliest_full_match_wins(db: Session, dev_user: User, matter: Matter) -> None:
    texts = ["dup page one", "dup page two"]
    first = _mk_doc(db, matter, pages=texts, created_at=_BASE)
    _mk_doc(db, matter, pages=texts, created_at=_BASE + dt.timedelta(minutes=1))
    new = _mk_doc(db, matter, pages=texts, created_at=_BASE + dt.timedelta(minutes=2))

    outcome = run_dedup(db, document=new)

    assert outcome.status == DedupStatus.DUPLICATE_OF.value
    # Two equal full-match candidates — the earliest (first) wins deterministically.
    assert outcome.against_document_id == first.id


def test_failed_candidate_is_ignored(db: Session, dev_user: User, matter: Matter) -> None:
    texts = ["identical body one", "identical body two"]
    # A FAILED earlier doc with the same text must not be a candidate.
    _mk_doc(db, matter, pages=texts, status=DocStatus.FAILED, created_at=_BASE)
    new = _mk_doc(db, matter, pages=texts, created_at=_BASE + dt.timedelta(minutes=1))

    outcome = run_dedup(db, document=new)

    assert outcome.status == DedupStatus.UNIQUE.value
    assert db.query(DedupDecision).count() == 0


# --------------------------------------------------------------------------------------
# resolve_dedup_decision
# --------------------------------------------------------------------------------------


def _pending_decision(db: Session, matter: Matter) -> DedupDecision:
    texts = ["r one", "r two"]
    _mk_doc(db, matter, pages=texts, created_at=_BASE)
    new = _mk_doc(db, matter, pages=texts, created_at=_BASE + dt.timedelta(minutes=1))
    outcome = run_dedup(db, document=new)
    decision = db.get(DedupDecision, outcome.decision_id)
    assert decision is not None
    return decision


def test_resolve_kept_records_and_audits(db: Session, dev_user: User, matter: Matter) -> None:
    decision = _pending_decision(db, matter)

    resolved = resolve_dedup_decision(
        db, user=dev_user, decision=decision, resolution=DedupResolution.KEPT
    )

    assert resolved.resolution == DedupResolution.KEPT.value
    assert resolved.resolved_by == dev_user.id
    assert resolved.resolved_at is not None
    events = db.query(AuditEvent).filter(AuditEvent.event_kind == "dedup_resolved").all()
    assert len(events) == 1
    assert events[0].payload["resolution"] == DedupResolution.KEPT.value
    assert events[0].payload["decision_id"] == str(decision.id)


def test_resolve_superseded_records(db: Session, dev_user: User, matter: Matter) -> None:
    decision = _pending_decision(db, matter)

    resolved = resolve_dedup_decision(
        db, user=dev_user, decision=decision, resolution=DedupResolution.SUPERSEDED
    )

    assert resolved.resolution == DedupResolution.SUPERSEDED.value
    assert resolved.resolved_by == dev_user.id


def test_second_resolve_raises_already_resolved(
    db: Session, dev_user: User, matter: Matter
) -> None:
    decision = _pending_decision(db, matter)
    resolve_dedup_decision(db, user=dev_user, decision=decision, resolution=DedupResolution.KEPT)

    try:
        resolve_dedup_decision(
            db, user=dev_user, decision=decision, resolution=DedupResolution.SUPERSEDED
        )
        raise AssertionError("expected DedupAlreadyResolved")
    except DedupAlreadyResolved:
        pass
    # Still kept — the second resolve is refused, not applied.
    db.refresh(decision)
    assert decision.resolution == DedupResolution.KEPT.value


def test_resolve_with_pending_argument_is_refused(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # `pending` is not a resolving disposition — guarded even though the API schema forbids it.
    decision = _pending_decision(db, matter)
    try:
        resolve_dedup_decision(
            db, user=dev_user, decision=decision, resolution=DedupResolution.PENDING
        )
        raise AssertionError("expected DedupAlreadyResolved")
    except DedupAlreadyResolved:
        pass
    db.refresh(decision)
    assert decision.resolution == DedupResolution.PENDING.value
