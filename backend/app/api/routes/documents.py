"""Document routes — browse the page store, reclassify, resolve dedup (04 §3 / corpus §4).

Thin by design (api_and_wire §4): validate, delegate to :mod:`app.corpus.ingest.classify` /
:mod:`app.corpus.ingest.dedup`, return a view-model. Tenancy is by construction — every read
goes through the firm-scoped session, so a cross-firm id 404s (never 403: an id must not leak
that a row exists in another tenant).

The ``GET /api/documents/{id}/pages`` endpoint is the M1-exit "browsable page store": paginated,
page-ordered access to the anchor target everything downstream resolves to.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.view_models import (
    dedup_decision_to_view,
    document_to_view,
    page_to_view,
)
from app.corpus.ingest.classify import reclassify_document
from app.corpus.ingest.dedup import DedupAlreadyResolved, resolve_dedup_decision
from app.models.enums import DedupResolution
from app.models.orm import CaseDocument, DedupDecision, DocumentPage, Matter, User
from app.models.schemas import DedupResolveRequest, ReclassifyRequest

router = APIRouter(prefix="/api", tags=["documents"])

# Upper bound on a page-listing page size — a client cannot ask for an unbounded scan.
_MAX_PAGE_LIMIT = 500

# Module-level dependency singletons (FastAPI pattern; avoids a Depends() call in a default
# argument, which ruff B008 flags — the call is evaluated once here, not per signature).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)


@router.get("/matters/{matter_id}/documents", response_model=None)
def list_documents(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
) -> JSONResponse:
    """List a matter's documents, ordered by ``(created_at, id)``.

    A matter outside the caller's firm scope is not found → ``404`` (never ``403``).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
        )
    documents = list(
        session.scalars(
            select(CaseDocument)
            .where(CaseDocument.matter_id == matter_id)
            .order_by(CaseDocument.created_at, CaseDocument.id)
        )
    )
    views = [document_to_view(doc) for doc in documents]
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"documents": [v.model_dump(mode="json") for v in views]},
    )


@router.get("/documents/{document_id}/pages", response_model=None)
def list_pages(
    document_id: uuid.UUID,
    offset: int = 0,
    limit: int = 100,
    session: Session = _TenantSession,
) -> JSONResponse:
    """Browse a document's pages, page-ordered and paginated (the M1-exit page store).

    ``limit`` is clamped to ``[0, 500]`` and ``offset`` floored at ``0`` so a client cannot request
    an unbounded or negative window. A document outside firm scope → ``404`` (never ``403``).
    """
    document = session.get(CaseDocument, document_id)
    if document is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "document_not_found", "detail": f"no document {document_id}"},
        )
    safe_offset = max(offset, 0)
    safe_limit = min(max(limit, 0), _MAX_PAGE_LIMIT)
    total = session.scalar(
        select(func.count())
        .select_from(DocumentPage)
        .where(DocumentPage.document_id == document_id)
    )
    pages = list(
        session.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_no)
            .offset(safe_offset)
            .limit(safe_limit)
        )
    )
    views = [page_to_view(page) for page in pages]
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "pages": [v.model_dump(mode="json") for v in views],
            "total": total or 0,
            "offset": safe_offset,
            "limit": safe_limit,
        },
    )


@router.post("/documents/{document_id}/reclassify", response_model=None)
def reclassify(
    document_id: uuid.UUID,
    body: ReclassifyRequest,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Apply an attorney's manual classification override; return the updated document view.

    A document outside firm scope → ``404`` (never ``403``).
    """
    document = session.get(CaseDocument, document_id)
    if document is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "document_not_found", "detail": f"no document {document_id}"},
        )
    reclassify_document(session, user=user, document=document, doc_type=body.doc_type)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=document_to_view(document).model_dump(mode="json"),
    )


@router.get("/matters/{matter_id}/dedup", response_model=None)
def list_dedup_decisions(
    matter_id: uuid.UUID,
    pending_only: bool = True,
    session: Session = _TenantSession,
) -> JSONResponse:
    """List a matter's quarantined dedup decisions (``pending_only`` filters to unresolved).

    A matter outside firm scope → ``404`` (never ``403``).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
        )
    stmt = (
        select(DedupDecision)
        .where(DedupDecision.matter_id == matter_id)
        .order_by(DedupDecision.created_at, DedupDecision.id)
    )
    if pending_only:
        stmt = stmt.where(DedupDecision.resolution == DedupResolution.PENDING.value)
    decisions = list(session.scalars(stmt))
    views = [dedup_decision_to_view(d) for d in decisions]
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"decisions": [v.model_dump(mode="json") for v in views]},
    )


@router.post("/dedup/{decision_id}/resolve", response_model=None)
def resolve_dedup(
    decision_id: uuid.UUID,
    body: DedupResolveRequest,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Record a human resolution (kept / superseded) on a pending dedup decision.

    A decision outside firm scope → ``404``; an already-resolved decision → ``409``
    (``dedup_already_resolved``).
    """
    decision = session.get(DedupDecision, decision_id)
    if decision is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "dedup_decision_not_found", "detail": f"no decision {decision_id}"},
        )
    try:
        resolve_dedup_decision(session, user=user, decision=decision, resolution=body.resolution)
    except DedupAlreadyResolved as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "dedup_already_resolved", "detail": str(exc)},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=dedup_decision_to_view(decision).model_dump(mode="json"),
    )
