"""Documents API: list, paginated page store, reclassify, dedup queue + resolve, tenant 404s.

Every cross-firm lookup returns ``404`` (never ``403``) — an id must not leak that a row exists
in another tenant. Requests run through ``make_client`` (a local app wrapping just the documents
router); rows are seeded directly via the shared in-memory session.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.documents import router as documents_router
from app.models.enums import DedupResolution, DedupStatus, DocStatus, DocType
from app.models.orm import CaseDocument, DedupDecision, DocumentPage, Matter

_BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _mk_doc(
    db: Session,
    matter: Matter,
    *,
    doc_type: DocType = DocType.MEDICAL_RECORD,
    status: DocStatus = DocStatus.CLASSIFIED,
    dedup_status: DedupStatus = DedupStatus.UNIQUE,
    needs_review: bool = False,
    confidence: float | None = 0.9,
    n_pages: int = 0,
    created_at: dt.datetime | None = None,
) -> CaseDocument:
    """Insert a CaseDocument (+ N blank-text pages) directly on ``matter``'s firm."""
    doc = CaseDocument(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=doc_type.value,
        source_label="d",
        filename="d.pdf",
        storage_key="k/d.pdf",
        page_count=n_pages,
        dedup_status=dedup_status.value,
        status=status.value,
        needs_review=needs_review,
        classification_confidence=confidence,
    )
    if created_at is not None:
        doc.created_at = created_at
    db.add(doc)
    db.flush()
    for i in range(1, n_pages + 1):
        db.add(
            DocumentPage(
                firm_id=matter.firm_id,
                document_id=doc.id,
                page_no=i,
                text=f"page {i} text",
                text_source="text_layer",
                zero_text=False,
            )
        )
    db.commit()
    return doc


def _mk_decision(
    db: Session,
    matter: Matter,
    document: CaseDocument,
    *,
    against: CaseDocument | None = None,
    status: DedupStatus = DedupStatus.DUPLICATE_OF,
    resolution: DedupResolution = DedupResolution.PENDING,
) -> DedupDecision:
    decision = DedupDecision(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        document_id=document.id,
        against_document_id=against.id if against else None,
        status=status.value,
        page_hash_matches=[[1, 1]],
        shingle_overlap=None,
        resolution=resolution.value,
    )
    db.add(decision)
    db.commit()
    return decision


# --------------------------------------------------------------------------------------
# GET /matters/{id}/documents
# --------------------------------------------------------------------------------------


def test_list_documents_ordered_by_created_at(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    d1 = _mk_doc(db, matter, created_at=_BASE)
    d2 = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=1))
    d3 = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=2))
    client = make_client(documents_router)

    resp = client.get(f"/api/matters/{matter.id}/documents")

    assert resp.status_code == 200
    ids = [d["id"] for d in resp.json()["documents"]]
    assert ids == [str(d1.id), str(d2.id), str(d3.id)]


def test_list_documents_unknown_matter_404(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    client = make_client(documents_router)
    resp = client.get(f"/api/matters/{uuid.uuid4()}/documents")
    assert resp.status_code == 404
    assert resp.json()["error"] == "matter_not_found"


def test_list_documents_cross_firm_matter_404(
    db: Session, firm_b_matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    # A Firm-B matter is invisible to the dev-attorney (Firm A) → 404, not 403.
    _mk_doc(db, firm_b_matter)
    client = make_client(documents_router)
    resp = client.get(f"/api/matters/{firm_b_matter.id}/documents")
    assert resp.status_code == 404
    assert resp.json()["error"] == "matter_not_found"


# --------------------------------------------------------------------------------------
# GET /documents/{id}/pages
# --------------------------------------------------------------------------------------


def test_pages_pagination_offset_limit_total(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    doc = _mk_doc(db, matter, n_pages=5)
    client = make_client(documents_router)

    resp = client.get(f"/api/documents/{doc.id}/pages?offset=1&limit=2")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["offset"] == 1
    assert body["limit"] == 2
    assert [p["page_no"] for p in body["pages"]] == [2, 3]


def test_pages_default_window_is_page_ordered(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    doc = _mk_doc(db, matter, n_pages=3)
    client = make_client(documents_router)

    resp = client.get(f"/api/documents/{doc.id}/pages")

    body = resp.json()
    assert body["total"] == 3
    assert body["offset"] == 0
    assert body["limit"] == 100
    assert [p["page_no"] for p in body["pages"]] == [1, 2, 3]


def test_pages_limit_clamped_to_500(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    doc = _mk_doc(db, matter, n_pages=1)
    client = make_client(documents_router)

    resp = client.get(f"/api/documents/{doc.id}/pages?limit=99999")

    assert resp.status_code == 200
    assert resp.json()["limit"] == 500


def test_pages_unknown_document_404(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    client = make_client(documents_router)
    resp = client.get(f"/api/documents/{uuid.uuid4()}/pages")
    assert resp.status_code == 404
    assert resp.json()["error"] == "document_not_found"


def test_pages_cross_firm_document_404(
    db: Session, firm_b_matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    doc = _mk_doc(db, firm_b_matter, n_pages=2)
    client = make_client(documents_router)
    resp = client.get(f"/api/documents/{doc.id}/pages")
    assert resp.status_code == 404
    assert resp.json()["error"] == "document_not_found"


# --------------------------------------------------------------------------------------
# POST /documents/{id}/reclassify
# --------------------------------------------------------------------------------------


def test_reclassify_happy_returns_updated_view(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.OTHER, needs_review=True, confidence=0.4)
    client = make_client(documents_router)

    resp = client.post(f"/api/documents/{doc.id}/reclassify", json={"doc_type": "medical_record"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_type"] == "medical_record"
    assert body["needs_review"] is False
    # Confidence describes the LLM verdict, not the override — unchanged.
    assert body["classification_confidence"] == 0.4
    db.refresh(doc)
    assert doc.doc_type == DocType.MEDICAL_RECORD.value


def test_reclassify_unknown_document_404(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    client = make_client(documents_router)
    resp = client.post(f"/api/documents/{uuid.uuid4()}/reclassify", json={"doc_type": "bill"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "document_not_found"


def test_reclassify_cross_firm_document_404(
    db: Session, firm_b_matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    doc = _mk_doc(db, firm_b_matter)
    client = make_client(documents_router)
    resp = client.post(f"/api/documents/{doc.id}/reclassify", json={"doc_type": "bill"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "document_not_found"
    # The Firm-B doc must be untouched by the refused request.
    db.refresh(doc)
    assert doc.doc_type == DocType.MEDICAL_RECORD.value


def test_reclassify_invalid_doc_type_422(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    doc = _mk_doc(db, matter)
    client = make_client(documents_router)
    resp = client.post(f"/api/documents/{doc.id}/reclassify", json={"doc_type": "not_a_type"})
    assert resp.status_code == 422  # schema validation rejects an unknown enum value


# --------------------------------------------------------------------------------------
# GET /matters/{id}/dedup + POST /dedup/{id}/resolve
# --------------------------------------------------------------------------------------


def test_dedup_queue_lists_pending_only_by_default(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    cand = _mk_doc(db, matter, created_at=_BASE)
    dup = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=1))
    resolved_doc = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=2))
    pending = _mk_decision(db, matter, dup, against=cand)
    _mk_decision(db, matter, resolved_doc, against=cand, resolution=DedupResolution.KEPT)
    client = make_client(documents_router)

    resp = client.get(f"/api/matters/{matter.id}/dedup")

    assert resp.status_code == 200
    decisions = resp.json()["decisions"]
    assert [d["id"] for d in decisions] == [str(pending.id)]
    assert decisions[0]["resolution"] == "pending"


def test_dedup_queue_pending_only_false_lists_all(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    cand = _mk_doc(db, matter, created_at=_BASE)
    dup = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=1))
    resolved_doc = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=2))
    _mk_decision(db, matter, dup, against=cand)
    _mk_decision(db, matter, resolved_doc, against=cand, resolution=DedupResolution.KEPT)
    client = make_client(documents_router)

    resp = client.get(f"/api/matters/{matter.id}/dedup?pending_only=false")

    assert resp.status_code == 200
    assert len(resp.json()["decisions"]) == 2


def test_dedup_queue_cross_firm_matter_404(
    db: Session, firm_b_matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    client = make_client(documents_router)
    resp = client.get(f"/api/matters/{firm_b_matter.id}/dedup")
    assert resp.status_code == 404
    assert resp.json()["error"] == "matter_not_found"


def test_resolve_decision_happy(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    cand = _mk_doc(db, matter, created_at=_BASE)
    dup = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=1))
    decision = _mk_decision(db, matter, dup, against=cand)
    client = make_client(documents_router)

    resp = client.post(f"/api/dedup/{decision.id}/resolve", json={"resolution": "kept"})

    assert resp.status_code == 200
    assert resp.json()["resolution"] == "kept"
    db.refresh(decision)
    assert decision.resolution == DedupResolution.KEPT.value


def test_resolve_decision_re_resolve_409(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    cand = _mk_doc(db, matter, created_at=_BASE)
    dup = _mk_doc(db, matter, created_at=_BASE + dt.timedelta(minutes=1))
    decision = _mk_decision(db, matter, dup, against=cand)
    client = make_client(documents_router)

    first = client.post(f"/api/dedup/{decision.id}/resolve", json={"resolution": "kept"})
    assert first.status_code == 200
    second = client.post(f"/api/dedup/{decision.id}/resolve", json={"resolution": "superseded"})
    assert second.status_code == 409
    assert second.json()["error"] == "dedup_already_resolved"


def test_resolve_decision_unknown_404(
    db: Session, matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    client = make_client(documents_router)
    resp = client.post(f"/api/dedup/{uuid.uuid4()}/resolve", json={"resolution": "kept"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "dedup_decision_not_found"


def test_resolve_decision_cross_firm_404(
    db: Session, firm_b_matter: Matter, make_client: Callable[..., TestClient]
) -> None:
    cand = _mk_doc(db, firm_b_matter, created_at=_BASE)
    dup = _mk_doc(db, firm_b_matter, created_at=_BASE + dt.timedelta(minutes=1))
    decision = _mk_decision(db, firm_b_matter, dup, against=cand)
    client = make_client(documents_router)

    resp = client.post(f"/api/dedup/{decision.id}/resolve", json={"resolution": "kept"})

    assert resp.status_code == 404
    assert resp.json()["error"] == "dedup_decision_not_found"
    db.refresh(decision)
    assert decision.resolution == DedupResolution.PENDING.value  # refused, unchanged
