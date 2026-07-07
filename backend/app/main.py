"""ClarionPI backend entrypoint.

Keeps the M0 ``/healthz`` liveness probe and mounts the matter API vertical slice. On startup
(non-prod only) it ensures the schema exists and seeds the dev firm + attorney so ``make dev`` and
tests work without a migration/login step — production uses Alembic migrations and the real M3
auth stack instead, so the seed/create-all path is gated off when ``APP_ENV=prod``.

Import rule (04 §5): nothing imports ``app.api`` except this module — the wire boundary is a leaf.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import seed_dev_users
from app.api.routes.analysis import router as analysis_router
from app.api.routes.auth import router as auth_router
from app.api.routes.documents import router as documents_router
from app.api.routes.drafting import router as drafting_router
from app.api.routes.evidence import router as evidence_router
from app.api.routes.gates import router as gates_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.matters import router as matters_router
from app.api.routes.provenance import router as provenance_router
from app.api.routes.uploads import router as uploads_router
from app.core.config import get_settings
from app.core.db import create_all_for_tests, get_engine, get_session_factory


def _seed_dev_environment() -> None:
    """Create schema + seed the dev firm/users in non-prod (convenience; see module doc).

    Seeds all three dev users (attorney/paralegal/admin, each with the dev password) via
    :func:`~app.api.deps.seed_dev_users`, so ``make dev`` supports both stub-mode and a real
    session-mode login per role without a migration/registration step.
    """
    if get_settings().app_env == "prod":
        return
    create_all_for_tests(get_engine())
    session = get_session_factory()()
    try:
        seed_dev_users(session)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: seed the dev environment on startup (non-prod only)."""
    _seed_dev_environment()
    yield


app = FastAPI(title="ClarionPI", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(matters_router)
app.include_router(gates_router)
app.include_router(uploads_router)
app.include_router(documents_router)
app.include_router(ingest_router)
app.include_router(evidence_router)
app.include_router(analysis_router)
app.include_router(drafting_router)
app.include_router(provenance_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
