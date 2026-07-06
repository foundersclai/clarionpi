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

from app.api.deps import seed_dev_firm_and_user
from app.api.routes.documents import router as documents_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.matters import router as matters_router
from app.api.routes.uploads import router as uploads_router
from app.core.config import get_settings
from app.core.db import create_all_for_tests, get_engine, get_session_factory


def _seed_dev_environment() -> None:
    """Create schema + seed the dev firm/user in non-prod (M0 convenience; see module doc)."""
    if get_settings().app_env == "prod":
        return
    create_all_for_tests(get_engine())
    session = get_session_factory()()
    try:
        seed_dev_firm_and_user(session)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: seed the dev environment on startup (non-prod only)."""
    _seed_dev_environment()
    yield


app = FastAPI(title="ClarionPI", lifespan=lifespan)
app.include_router(matters_router)
app.include_router(uploads_router)
app.include_router(documents_router)
app.include_router(ingest_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
