"""Ingest route — kick off Phase 0 for a matter and stream it over SSE (04 §3 / corpus §4).

Thin by design (api_and_wire §4): resolve the matter on the tenant-scoped session, then hand a
:class:`~fastapi.responses.StreamingResponse` the :func:`~app.corpus.ingest.phase0.run_phase0`
generator. All the work — classify, page-build, dedup, the gate step, the run log — lives in the
runner; this module only wires storage/OCR/provider and streams the frames.

M1 runs Phase 0 **inline in the request** (ADR-0002): there is no Procrastinate worker yet
(that lands with the M3 orchestrator), so the SSE request itself is the job. FastAPI keeps
yield-dependencies open until the stream finishes, so the tenant session stays alive for the
whole run — no special handling needed.

Storage is injected via :func:`~app.api.routes.uploads.get_object_storage` (reused so tests
override one dependency); OCR and the provider get their own overridable deps here so tests can
inject :class:`~app.corpus.ocr.FakeOcr` / :class:`~app.core.llm_provider.ScriptedProvider`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.routes.uploads import get_object_storage
from app.core.llm_provider import LLMProvider, get_llm_provider
from app.core.storage import ObjectStorage
from app.corpus.ingest.phase0 import run_phase0
from app.corpus.ocr import OcrEngine, get_ocr_engine
from app.models.orm import Matter, User

router = APIRouter(prefix="/api", tags=["ingest"])


def get_ocr() -> OcrEngine:
    """Return the process OCR engine. Tests override this with :class:`FakeOcr`."""
    return get_ocr_engine()


def get_provider() -> LLMProvider:
    """Return the wired LLM provider. Tests override this with :class:`ScriptedProvider`."""
    return get_llm_provider()


# Module-level dependency singletons (FastAPI pattern; avoids a Depends() call in a default
# argument, which ruff B008 flags — the call is evaluated once here, not per signature).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)
_ObjectStorage = Depends(get_object_storage)
_Ocr = Depends(get_ocr)
_Provider = Depends(get_provider)


@router.post("/matters/{matter_id}/ingest/run", response_model=None)
def run_ingest(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    storage: ObjectStorage = _ObjectStorage,
    ocr: OcrEngine = _Ocr,
    provider: LLMProvider = _Provider,
) -> StreamingResponse | JSONResponse:
    """Run Phase 0 for ``matter_id`` and stream its SSE frames.

    A matter outside the caller's firm scope is not found → ``404`` (never ``403``: an id must not
    leak that a row exists in another tenant). Otherwise the response body is the Phase-0 SSE
    stream (``text/event-stream``); FastAPI holds the tenant session open until the stream ends.
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
        )
    return StreamingResponse(
        run_phase0(
            session,
            matter=matter,
            user=user,
            storage=storage,
            ocr=ocr,
            provider=provider,
        ),
        media_type="text/event-stream",
    )
