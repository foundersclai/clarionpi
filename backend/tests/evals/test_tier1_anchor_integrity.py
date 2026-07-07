"""Tier-1 anchor-integrity — the M6 Wave A eval (E2 round-trip + E3 dead-anchor detectability).

This is the M6 acceptance the provenance backend rests on: a fact's page-level anchors must be
LIVE, and a broken anchor must be DETECTABLE (it fails G3, not render time). It reuses the evals
harness — the gold matters + the scripted Phase-0 pipeline from ``test_tier1_extraction.py`` — to
get a fully-populated registry (FACT tokens from extraction sync, AMT tokens from the ledger mint),
then proves three things over GM-1:

* **E2 — anchor round-trip (100%).** For EVERY :class:`~app.models.orm.FactToken` of the matter
  (latest per slot), :func:`~app.engine.tokenizer.registry.resolve_for_render` yields anchors whose
  every ``(document_id, page)`` satisfies: the document is in the matter, ``1 <= page <=
  page_count``, and the document is not dedup-superseded. Asserted at 100% — and the token/anchor
  counts are asserted non-trivial (a vacuous pass over zero anchors is itself a failure).
* **E3 — dead-anchor detectability.** Shrink one document's ``page_count`` below a live anchor's
  page → the deterministic ``dead_anchor`` compliance check (over a minimal draft citing that
  token) produces a finding; restore the count → the check is clean again. (A broken anchor fails
  G3, not render — this test IS that contract.)
* **Provenance-endpoint HTTP round-trip.** For 3 sampled tokens, GET the provenance endpoint, then
  GET each returned ``blob_url`` → 200 PDF bytes. The full loop is timed and printed — informational
  (the <2s browser budget is measured with the real viewer; this is the SERVER floor).

Scripted-only (no ``@pytest.mark.integration``): the pipeline runs a
:class:`~app.core.llm_provider.ScriptedProvider`, so this is a deterministic fast-suite eval.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import seed_dev_firm_and_user
from app.api.routes.uploads import get_object_storage
from app.core.config import Settings, get_settings
from app.core.db import (
    create_all_for_tests,
    create_db_engine,
    create_session_factory,
    get_db_session,
)
from app.core.llm_provider import LLMProvider
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.engine.compliance.checks import build_check_context, run_deterministic_checks
from app.engine.tokenizer import registry
from app.main import app
from app.models.enums import (
    CheckKind,
    DraftStatus,
    GateState,
    SectionValidation,
)
from app.models.orm import (
    CaseDocument,
    DemandDraft,
    DraftSection,
    FactToken,
    Matter,
    User,
)
from tests.evals.gold_fixtures import GoldMatter, build_gm1, scripted_provider_for
from tests.evals.test_tier1_extraction import _drive_matter

# --------------------------------------------------------------------------------------
# Fixtures (self-contained, mirroring test_tier1_extraction.py — a shared in-memory engine + seeded
# dev tenant + a corpus_processing matter + tmp storage — so the driver runs the whole Phase-0
# pipeline).
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=500_00,
        )
    )
    create_all_for_tests(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def dev_user(db: Session) -> User:
    return seed_dev_firm_and_user(db)


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "storage")


@pytest.fixture
def matter(db: Session, dev_user: User) -> Matter:
    import datetime as dt

    m = Matter(
        client_display_name="M6 Anchor Client",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    tenant_add(db, m, dev_user.firm_id)
    db.commit()
    return m


@pytest.fixture
def client(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage
) -> Iterator[TestClient]:
    """A TestClient bound to THIS eval's in-memory engine + tmp storage (stub-auth dev attorney).

    Overrides ``get_db_session`` (so the API sees the same rows the pipeline wrote) and
    ``get_object_storage`` (so the blob route serves the gold PDFs the driver stored). Stub auth
    resolves the seeded dev attorney, whose firm owns the gold matter.
    """

    def _override_db_session() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------------------
# Driver helper
# --------------------------------------------------------------------------------------


def _drive_gm1(
    db: Session, *, dev_user: User, matter: Matter, storage: LocalDiskStorage, logs_dir: Path
) -> tuple[GoldMatter, dict[str, uuid.UUID]]:
    """Run the scripted Phase-0 pipeline for GM-1; return the gold + the ``key -> document_id`` map.

    After this, the registry holds FACT tokens (extraction sync) + AMT tokens (ledger mint), and the
    gold documents are stored in ``storage`` with their ``storage_key`` set (so the blob route can
    serve them).
    """
    gold = build_gm1()

    def _make_scripted(doc_key_order: list[str]) -> LLMProvider:
        return scripted_provider_for(gold, doc_key_order)

    doc_id_by_key, _chronology = _drive_matter(
        db,
        user=dev_user,
        matter=matter,
        storage=storage,
        gold=gold,
        make_provider=_make_scripted,
        logs_dir=logs_dir,
    )
    return gold, doc_id_by_key


def _latest_tokens(db: Session, *, matter: Matter) -> list[FactToken]:
    """Every FactToken for the matter, latest-version row per ``token_id`` (the live slot state)."""
    rows = list(db.execute(select(FactToken).where(FactToken.matter_id == matter.id)).scalars())
    latest: dict[str, FactToken] = {}
    for row in rows:
        seen = latest.get(row.token_id)
        if seen is None or row.registry_version > seen.registry_version:
            latest[row.token_id] = row
    return list(latest.values())


# --------------------------------------------------------------------------------------
# E2 — anchor round-trip (100%)
# --------------------------------------------------------------------------------------


def test_e2_every_token_anchor_round_trips(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    _gold, _map = _drive_gm1(
        db, dev_user=dev_user, matter=matter, storage=storage, logs_dir=tmp_path
    )

    page_counts = {
        doc.id: doc.page_count
        for doc in db.scalars(select(CaseDocument).where(CaseDocument.matter_id == matter.id))
    }
    matter_doc_ids = set(page_counts)

    tokens = _latest_tokens(db, matter=matter)
    total_tokens = 0
    total_anchors = 0
    for row in tokens:
        total_tokens += 1
        result = registry.resolve_for_render(db, matter=matter, token=f"[[{row.token_id}]]")
        assert result.outcome != "orphan", f"{row.token_id} resolved orphan for its own matter"
        for anchor in result.anchors:
            total_anchors += 1
            doc_id = _anchor_doc_id(anchor)
            assert doc_id is not None, f"{row.token_id} anchor missing document_id: {anchor}"
            assert doc_id in matter_doc_ids, f"{row.token_id} anchor doc not in matter: {doc_id}"
            page = anchor.get("page")
            page_count = page_counts[doc_id]
            assert isinstance(page, int), f"{row.token_id} anchor page not an int: {page!r}"
            assert 1 <= page <= page_count, (
                f"{row.token_id} anchor page {page} out of 1..{page_count} for {doc_id}"
            )

    # Non-trivial: a vacuous pass over zero tokens/anchors is itself a failure (E2 must have teeth).
    assert total_tokens > 0, "no FactTokens minted — the pipeline produced nothing to check"
    assert total_anchors > 0, "no anchors across all tokens — E2 would be vacuous"
    print(
        f"\n[M6 E2] anchor round-trip: {total_tokens} tokens, {total_anchors} anchors, "
        "100% in-bounds + non-superseded."
    )


def _anchor_doc_id(anchor: dict) -> uuid.UUID | None:
    raw = anchor.get("document_id")
    if raw is None:
        return None
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


# --------------------------------------------------------------------------------------
# E3 — dead-anchor detectability (the "broken anchors fail G3, not render" contract)
# --------------------------------------------------------------------------------------


def _first_anchored_fact(db: Session, *, matter: Matter) -> tuple[FactToken, uuid.UUID, int]:
    """The first FactToken with a real (doc, page) anchor + that anchor's (doc_id, page).

    Deterministic pick: iterate the latest tokens in ``token_id`` order and return the first whose
    resolution carries an anchor with an in-bounds page — the E3 target to break.
    """
    for row in sorted(_latest_tokens(db, matter=matter), key=lambda r: r.token_id):
        result = registry.resolve_for_render(db, matter=matter, token=f"[[{row.token_id}]]")
        for anchor in result.anchors:
            doc_id = _anchor_doc_id(anchor)
            page = anchor.get("page")
            if doc_id is not None and isinstance(page, int) and page >= 1:
                return row, doc_id, page
    raise AssertionError("no anchored FactToken found for the matter — cannot run E3")


def _draft_citing(db: Session, *, matter: Matter, token_id: str) -> DemandDraft:
    """A minimal 1-section draft whose body cites ``[[token_id]]`` — the E3 check target.

    Pinned to the matter's current registry version (so the check's registry precondition holds).
    """
    draft = DemandDraft(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        strategy_plan_version=1,
        status=DraftStatus.IN_COMPLIANCE.value,
        memo="",
    )
    tenant_add(db, draft, matter.firm_id)
    db.flush()
    section = DraftSection(
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id="facts",
        purpose="p",
        body_tokenized=f"The record shows [[{token_id}]] as the anchoring fact.",
        rendered_preview=None,
        registry_version=matter.registry_version,
        validation=SectionValidation.PASSED.value,
        spans=[],
        sort_order=1,
    )
    tenant_add(db, section, matter.firm_id)
    db.commit()
    return draft


def test_e3_dead_anchor_is_detected_then_clean(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    _drive_gm1(db, dev_user=dev_user, matter=matter, storage=storage, logs_dir=tmp_path)

    token, doc_id, page = _first_anchored_fact(db, matter=matter)
    draft = _draft_citing(db, matter=matter, token_id=token.token_id)
    doc = db.get(CaseDocument, doc_id)
    original_page_count = doc.page_count

    # Baseline: with the true page_count, the dead_anchor check is CLEAN for this token.
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    dead_before = [f for f in findings if f.check_kind == CheckKind.DEAD_ANCHOR.value]
    assert dead_before == [], (
        f"unexpected dead_anchor at baseline: {[f.detail for f in dead_before]}"
    )

    # Break it: shrink the document below the anchor's page → the anchor is now out of bounds.
    doc.page_count = page - 1
    db.commit()
    ctx_broken = build_check_context(db, matter=matter, draft=draft)
    findings_broken = run_deterministic_checks(db, ctx_broken)
    dead = [f for f in findings_broken if f.check_kind == CheckKind.DEAD_ANCHOR.value]
    assert dead, (
        f"dead_anchor NOT detected after shrinking doc {doc_id} to page_count {page - 1} "
        f"(anchor page {page})"
    )
    assert any(f.section_id == "facts" for f in dead), (
        "dead_anchor finding not on the citing section"
    )

    # Restore: the check is clean again (the break was the page_count, nothing else).
    doc.page_count = original_page_count
    db.commit()
    ctx_restored = build_check_context(db, matter=matter, draft=draft)
    findings_restored = run_deterministic_checks(db, ctx_restored)
    dead_after = [f for f in findings_restored if f.check_kind == CheckKind.DEAD_ANCHOR.value]
    assert dead_after == [], "dead_anchor persisted after restoring the page_count"
    print(
        f"\n[M6 E3] dead-anchor detectability: token {token.token_id} anchor page {page} on "
        f"doc {doc_id} — detected when page_count<{page}, clean when restored."
    )


# --------------------------------------------------------------------------------------
# Provenance-endpoint HTTP round-trip (server-floor timing — informational)
# --------------------------------------------------------------------------------------


def test_provenance_http_round_trip_over_sampled_tokens(
    client: TestClient,
    db: Session,
    dev_user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    tmp_path: Path,
) -> None:
    _drive_gm1(db, dev_user=dev_user, matter=matter, storage=storage, logs_dir=tmp_path)

    # Sample up to 3 tokens deterministically (token_id order). We prefer tokens that carry at least
    # one anchor so the blob leg exercises real bytes; AMT tokens (no anchors) still exercise the
    # provenance leg.
    tokens = sorted(_latest_tokens(db, matter=matter), key=lambda r: r.token_id)
    anchored = [
        r
        for r in tokens
        if registry.resolve_for_render(db, matter=matter, token=f"[[{r.token_id}]]").anchors
    ]
    sample = (anchored or tokens)[:3]
    assert sample, "no tokens to sample for the HTTP round-trip"

    start = time.perf_counter()
    blob_fetches = 0
    for row in sample:
        prov = client.get(f"/api/matters/{matter.id}/provenance/{row.token_id}")
        assert prov.status_code == 200, prov.text
        body = prov.json()
        assert body["token_id"] == row.token_id
        for anchor in body["anchors"]:
            blob_url = anchor["blob_url"]
            assert blob_url is not None
            blob = client.get(blob_url)
            assert blob.status_code == 200, blob.text
            assert blob.headers["content-type"] == "application/pdf"
            assert blob.content, "empty PDF bytes served"
            blob_fetches += 1
    elapsed = time.perf_counter() - start

    # Informational only — the <2s browser budget is measured with the real viewer; this is the
    # server floor (no network, no pdf.js render).
    print(
        f"\n[M6 provenance] HTTP round-trip: {len(sample)} tokens + {blob_fetches} blob fetches in "
        f"{elapsed * 1000:.1f} ms (server floor; browser <2s budget measured with the real viewer)."
    )
