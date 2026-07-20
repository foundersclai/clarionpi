"""Provenance routes (M6 Wave A) — the document blob store + token→anchor provenance read.

Thin by design (api_and_wire §4): tenancy through ``get_tenant_session`` (a cross-firm id 404s,
never 403 — existence must not leak), all resolution logic in the owning module
(:mod:`app.engine.tokenizer.registry` — the single resolution authority), and the JSON response
passed through :func:`~app.api.wire_guard.scan_wire_payload` before it leaves (invariant 11).

Two surfaces back the M6 provenance viewer:

* ``GET /api/documents/{id}/blob`` — the whole-document PDF bytes, APP-SERVED over this
  authenticated tenant-scoped route (the ``local`` storage backend has no presign — pdf.js seeks
  client-side within the served bytes). Every fetch writes a ``phi_access`` audit event BEFORE the
  bytes leave: the page-read is the audited PHI event (inv 7), not a token lookup — so the
  deliberate GET-write mirrors the artifact-download precedent
  (``routes/drafting.get_artifact_download``).
* ``GET /api/matters/{id}/provenance/{token_id}`` — resolve one BARE token id (e.g. ``FACT_7``) to
  its display form + verification outcome + server-enriched anchors (each carrying the
  ready-to-fetch ``blob_url``, the target document's ``page_count`` + ``filename`` + ``doc_type``
  — the viewer labels a source page by name, never a bare uuid — and a dedup-``superseded``
  flag). This is a pure read: NO audit here (the pinned decision — the token lookup is not the PHI
  event; the blob fetch is), and NO ``live_ledger_hash`` (the viewer shows provenance, not the G3
  amount-drift verdict — an AMT's outcome comes straight from its stored status, never a re-hash).

Highlights are page-level at v1: ``bbox`` is never populated by the current pipeline, but the wire
still carries ``bbox: null`` per anchor for the S1-vendor future (a bbox-emitting extractor).

Typed error mapping:

| condition                          | HTTP | body ``error``          |
|------------------------------------|------|-------------------------|
| document not in firm scope         | 404  | ``document_not_found``  |
| document has no stored blob        | 404  | ``blob_missing``        |
| matter not in firm scope           | 404  | ``matter_not_found``    |
| token id not ``^(FACT|AMT|CITE|EX)_\\d+$`` | 422 | ``invalid_token_id``  |
| token resolves to an orphan        | 404  | ``token_not_found``     |
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.routes.uploads import get_object_storage
from app.api.wire_guard import scan_wire_payload
from app.core.audit import record_event
from app.core.storage import ObjectStorage, StoredObjectNotFound
from app.engine.tokenizer import registry
from app.models.enums import DedupResolution
from app.models.orm import CaseDocument, DedupDecision, FactToken, Matter, User

router = APIRouter(prefix="/api", tags=["provenance"])

# Module-level dependency singletons (ruff B008; evaluated once — see routes/documents.py).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)
_ObjectStorage = Depends(get_object_storage)

# The BARE token-id grammar this endpoint accepts (``FACT_7``), the un-bracketed form of the
# registry's canonical ``[[FACT_7]]`` token. A miss is a 422 ``invalid_token_id`` (a malformed id is
# a client error, not a not-found) — this deliberately rejects the bracketed/lower-case shapes so
# nothing token-shaped is ever accepted on the path either.
_BARE_TOKEN_RE = re.compile(r"^(FACT|AMT|CITE|EX)_\d+$")


def _blob_url(document_id: uuid.UUID) -> str:
    """The ready-to-fetch blob route for a document — the FE never constructs URLs itself."""
    return f"/api/documents/{document_id}/blob"


def _sanitize_filename(filename: str) -> str:
    """Strip quotes/newlines so a filename can be spliced into a ``Content-Disposition`` header.

    A stored filename could (in principle) carry a ``"`` or newline that would break out of the
    quoted header value / inject a second header; drop those characters so the header is always the
    single ``inline; filename="..."`` we intend.
    """
    return filename.replace('"', "").replace("\r", "").replace("\n", "")


@router.get("/documents/{document_id}/blob", response_model=None)
def get_document_blob(
    document_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    storage: ObjectStorage = _ObjectStorage,
) -> Response | JSONResponse:
    """Serve a document's whole-PDF bytes, tenant-scoped, auditing the PHI page-read first.

    A cross-firm document → ``404 document_not_found`` (never 403; existence must not leak). A
    document with no ``storage_key`` — or whose key has no stored object — → ``404 blob_missing`` (a
    failed/expired ingest never stored a blob). Otherwise the raw ``application/pdf`` bytes are
    served with an ``inline`` ``Content-Disposition`` (pdf.js renders inline + seeks client-side).

    The fetch is audited (``phi_access``) BEFORE the bytes leave and committed here — the
    deliberate GET-write mirrors the artifact-download precedent (``get_artifact_download``):
    serving a case page IS the audited PHI access event (inv 7), so a blob read that returns bytes
    must always leave an audit row (two fetches → two rows). This is a raw bytes ``Response`` (not
    JSON) — it is NOT wire-scanned (PDF bytes are not a token surface).
    """
    document = session.get(CaseDocument, document_id)
    if document is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "document_not_found", "detail": f"no document {document_id}"},
        )
    if document.storage_key is None:
        return _blob_missing(document_id)
    try:
        data = storage.get(document.storage_key)
    except StoredObjectNotFound:
        return _blob_missing(document_id)

    filename = _sanitize_filename(document.filename or "document.pdf")
    # Audit the PHI page-read BEFORE returning the bytes, and commit here — the deliberate GET-write
    # (inv 7: the served page is the audited event, not the token lookup), mirroring
    # ``get_artifact_download``.
    record_event(
        session,
        firm_id=document.firm_id,
        actor_id=user.id,
        event_kind="phi_access",
        payload={
            "document_id": str(document.id),
            "filename": document.filename,
            "surface": "provenance_viewer",
        },
    )
    session.commit()
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _blob_missing(document_id: uuid.UUID) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "blob_missing", "detail": f"no stored blob for document {document_id}"},
    )


def _latest_source(session: Session, *, matter: Matter, token_id: str) -> str | None:
    """The ``source`` of the latest-version :class:`FactToken` row for ``token_id`` (or ``None``).

    Resolution (``resolve_for_render``) carries the display/value/anchors/outcome but not the row's
    provenance ``source``; the wire exposes it (``extractor|attorney|rules``) so the viewer can
    label who asserted the fact. Read from the highest-``registry_version`` row for the slot — the
    same latest-wins rule resolution uses.
    """
    rows = list(
        session.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id,
                FactToken.token_id == token_id,
            )
        ).scalars()
    )
    if not rows:
        return None
    return max(rows, key=lambda r: r.registry_version).source


def _enrich_anchor(
    anchor: dict, *, documents: dict[uuid.UUID, CaseDocument], superseded: frozenset[uuid.UUID]
) -> dict:
    """One wire anchor: the (doc, page) plus server-joined document facts + the ready blob url.

    ``bbox`` is always ``null`` at v1 (page-level highlights — the pipeline emits no region),
    carried on the wire for the S1-vendor future. ``document_id`` is normalized to a string;
    ``page_count`` / ``filename`` / ``doc_type`` are joined from the anchor's target document so
    the viewer can label a source page by NAME ("01_police_report.pdf · page 2"), never a bare
    uuid (all ``None`` if the anchor names a document not in the matter — a broken anchor a G3
    ``dead_anchor`` check would catch, surfaced here, not hidden).
    """
    doc_id = _anchor_document_id(anchor)
    doc = documents.get(doc_id) if doc_id is not None else None
    return {
        "document_id": str(doc_id) if doc_id is not None else None,
        "page": anchor.get("page"),
        "bbox": None,
        "blob_url": _blob_url(doc_id) if doc_id is not None else None,
        "page_count": doc.page_count if doc is not None else None,
        "filename": doc.filename if doc is not None else None,
        "doc_type": doc.doc_type if doc is not None else None,
        "superseded": doc_id in superseded if doc_id is not None else False,
    }


def _anchor_document_id(anchor: dict) -> uuid.UUID | None:
    """Parse an anchor dict's ``document_id`` (tolerating str or UUID), or ``None``."""
    raw = anchor.get("document_id")
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _documents_by_id(session: Session, *, matter: Matter) -> dict[uuid.UUID, CaseDocument]:
    """``document_id -> CaseDocument`` for the matter — the anchor-enrichment join target.

    One query feeds every anchor's ``page_count`` / ``filename`` / ``doc_type`` on the wire.
    """
    return {
        doc.id: doc
        for doc in session.scalars(select(CaseDocument).where(CaseDocument.matter_id == matter.id))
    }


def _superseded_document_ids(session: Session, *, matter: Matter) -> frozenset[uuid.UUID]:
    """Document ids dropped by a dedup-superseded decision (mirrors compliance/money rule)."""
    return frozenset(
        session.scalars(
            select(DedupDecision.document_id).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.resolution == DedupResolution.SUPERSEDED.value,
            )
        )
    )


@router.get("/matters/{matter_id}/provenance/{token_id}", response_model=None)
def get_token_provenance(
    matter_id: uuid.UUID,
    token_id: str,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """Resolve one BARE token id to its display form + outcome + server-enriched anchors.

    A cross-firm matter → ``404 matter_not_found`` first (existence must not leak). A ``token_id``
    that is not a bare registry id (``^(FACT|AMT|CITE|EX)_\\d+$``) → ``422 invalid_token_id`` (a
    malformed id is a client error; the bracketed/lower-case shapes are rejected too — nothing
    token-shaped is accepted on the path). The bare id is wrapped to the registry's bracketed form
    and resolved via :func:`~app.engine.tokenizer.registry.resolve_for_render` WITHOUT a
    ``live_ledger_hash`` — the viewer shows provenance, not the G3 amount-drift verdict, so an
    AMT's outcome is its stored status, never a re-hash. An orphan (nothing resolves) → ``404
    token_not_found``; else a 200 with the resolved display/outcome + each anchor enriched
    server-side (``page_count`` + ``filename`` + ``doc_type`` + ``superseded`` joined from the
    anchor's document, plus the ready-to-fetch ``blob_url``). NO audit here (the token lookup is
    not the PHI event — the blob fetch is). The payload is wire-scanned (inv 11).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
        )
    if _BARE_TOKEN_RE.fullmatch(token_id) is None:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_token_id", "detail": f"not a bare token id: {token_id!r}"},
        )

    # Wrap the bare id to the registry's bracketed token, then resolve.
    # ``token_str``/``parse_token`` is the sanctioned round-trip; the regex already validated shape.
    kind, ordinal = registry.parse_token(f"[[{token_id}]]")
    token = registry.token_str(kind, ordinal)
    result = registry.resolve_for_render(session, matter=matter, token=token)
    if result.outcome == "orphan":
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "token_not_found", "detail": f"token {token_id} does not resolve"},
        )

    documents = _documents_by_id(session, matter=matter)
    superseded = _superseded_document_ids(session, matter=matter)
    payload = {
        "token_id": token_id,
        "display_form": result.display_form,
        "outcome": result.outcome,
        "source": _latest_source(session, matter=matter, token_id=token_id),
        "anchors": [
            _enrich_anchor(anchor, documents=documents, superseded=superseded)
            for anchor in result.anchors
        ],
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=scan_wire_payload(payload, where="provenance.token"),
    )
