"""Tier-1 extraction-fidelity tests — the M2 EXIT CRITERION, proved two ways.

Scripted mode (fast suite, deterministic): a :class:`~app.core.llm_provider.ScriptedProvider`
plays a "perfect-ish" extractor over the two gold matters, driving the WHOLE Phase-0 pipeline
(classify → pages → dedup → extract → merge → registry sync → ledger AMT mint) plus a chronology
build, then :func:`~tests.evals.tier1.score_matter` asserts the M2 exit facts. This proves the
harness math + pipeline plumbing without a live model, and the printed reports are the CI-run
Tier-1 numbers.

Live mode (``@pytest.mark.integration``, skipped without ``ANTHROPIC_API_KEY``): the SAME gold +
SAME scorer against :class:`~app.core.llm_provider.AnthropicProvider`, producing the real S2
datapoint. Run it explicitly::

    ANTHROPIC_API_KEY=... LLM_PROVIDER=anthropic \\
      .venv/bin/pytest -m integration tests/evals/test_tier1_extraction.py

Both paths share :func:`_drive_matter`, which uploads the gold docs, runs the pipeline to
exhaustion, resolves any duplicate ``SUPERSEDED``, and builds the chronology (narratives OFF).
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import seed_dev_firm_and_user
from app.core.config import Settings, get_settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.llm_provider import AnthropicProvider, LLMProvider
from app.core.matter_logs import MatterRunLogger
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.corpus.ingest.dedup import resolve_dedup_decision
from app.corpus.ingest.phase0 import run_phase0
from app.corpus.ocr import FakeOcr
from app.engine.brain1.chronology import ChronologyBuildOutcome, build_chronology
from app.engine.orchestrator.phase0_completion import handle_phase0_completion
from app.models.enums import DedupResolution, DedupStatus, DocStatus, DocType, GateState
from app.models.orm import CaseDocument, DedupDecision, LlmCall, Matter, MatterBudget, User
from tests.evals.gold_fixtures import GoldMatter, build_gm1, build_gm2, scripted_provider_for
from tests.evals.tier1 import Tier1Report, score_matter

# A generous per-matter cap for the LIVE run so real extraction never trips the default $25 cap.
_LIVE_BUDGET_CENTS = 500_00

# Base upload timestamp — docs get second-spaced created_at from this so (created_at, id) ordering
# is deterministic (see _upload_gold_docs). A fixed instant keeps runs reproducible.
_BASE_UPLOAD_TS = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.UTC)


# --------------------------------------------------------------------------------------
# Fixtures (self-contained — there is no evals/conftest.py in this wave's ownership, so we build
# the in-memory engine / open session / seeded dev tenant / corpus_processing matter / tmp storage
# here directly, mirroring tests/corpus/conftest.py).
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin APP_ENV=test so any process-global engine/storage default stays out of the repo."""
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=_LIVE_BUDGET_CENTS,
        )
    )
    create_all_for_tests(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """One open (unscoped) session for pipeline setup + assertions."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def dev_user(db: Session) -> User:
    """The seeded dev attorney (Firm A) attached to the open ``db`` session."""
    return seed_dev_firm_and_user(db)


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "storage")


@pytest.fixture
def matter(db: Session, dev_user: User) -> Matter:
    """A Firm-A AZ mva matter in ``corpus_processing`` — the Phase-0 entry state."""
    m = Matter(
        client_display_name="S2 Gold Client",
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


# --------------------------------------------------------------------------------------
# Shared driver
# --------------------------------------------------------------------------------------


def _upload_gold_docs(
    db: Session, *, user: User, matter: Matter, storage: LocalDiskStorage, gold: GoldMatter
) -> None:
    """Store each gold PDF + create an UPLOADED, doc_type=OTHER CaseDocument keyed by its gold key.

    The fixture-doc KEY is stashed in ``filename``/``source_label`` so the driver can recover the
    doc↔key mapping after the runner processes them (classify will type each doc from OTHER).

    ``created_at`` is set to strictly-increasing per-doc timestamps in the gold's dict order, so the
    ``(created_at, id)`` order the runner + dedup use is DETERMINISTIC (not decided by the random
    ``id`` tiebreak). This pins two things for GM-2: dedup flags the LATER byte-copy (``bills_dup``,
    inserted after ``bills_1``) as ``DUPLICATE_OF`` — not whichever UUID happened to sort first —
    and the scripted-call-order derivation (docs in dict order) holds exactly.
    """
    for index, (doc_key, (pdf_bytes, _doc_type)) in enumerate(gold.documents.items()):
        storage_key = f"matters/{matter.id}/{uuid.uuid4()}.pdf"
        storage.put(storage_key, pdf_bytes)
        doc = CaseDocument(
            matter_id=matter.id,
            doc_type=DocType.OTHER.value,
            source_label=doc_key,
            filename=doc_key,
            storage_key=storage_key,
            dedup_status=DedupStatus.UNIQUE.value,
            status=DocStatus.UPLOADED.value,
            # Second-spaced so SQLite's second-resolution DateTime keeps them strictly ordered.
            created_at=_BASE_UPLOAD_TS + dt.timedelta(seconds=index),
        )
        tenant_add(db, doc, user.firm_id)
    db.commit()


def _doc_key_order_and_map(
    db: Session, *, matter: Matter
) -> tuple[list[str], dict[str, uuid.UUID]]:
    """The fixture-doc keys in ``(created_at, id)`` order + the ``key -> document_id`` map.

    This is the ACTUAL order ``run_phase0`` processes pending docs (its ``_pending_documents``
    orders by ``(created_at, id)``), so the scripted FIFO must be built in exactly this order.
    """
    docs = list(
        db.execute(
            select(CaseDocument)
            .where(CaseDocument.matter_id == matter.id)
            .order_by(CaseDocument.created_at, CaseDocument.id)
        ).scalars()
    )
    doc_key_order = [doc.filename for doc in docs]
    doc_id_by_key = {doc.filename: doc.id for doc in docs}
    return doc_key_order, doc_id_by_key


def _resolve_duplicates_superseded(db: Session, *, user: User, matter: Matter) -> None:
    """Resolve every PENDING dedup decision on a DUPLICATE_OF doc as SUPERSEDED (GM-2's dup)."""
    decisions = list(
        db.execute(
            select(DedupDecision).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.status == DedupStatus.DUPLICATE_OF.value,
                DedupDecision.resolution == DedupResolution.PENDING.value,
            )
        ).scalars()
    )
    for decision in decisions:
        resolve_dedup_decision(
            db, user=user, decision=decision, resolution=DedupResolution.SUPERSEDED
        )


def _drive_matter(
    db: Session,
    *,
    user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    gold: GoldMatter,
    make_provider: Callable[[list[str]], LLMProvider],
    logs_dir: Path,
) -> tuple[dict[str, uuid.UUID], ChronologyBuildOutcome]:
    """Run the Phase-0 pipeline for ``gold``, resolve dups, build the chronology (no narrative).

    ``make_provider`` receives the resolved ``(created_at, id)`` doc-key order and returns the
    provider to run with (scripted mode uses it to build the exact FIFO script; live mode ignores
    it). Returns the ``key -> document_id`` map and the chronology outcome for the scorer.
    """
    _upload_gold_docs(db, user=user, matter=matter, storage=storage, gold=gold)
    doc_key_order, doc_id_by_key = _doc_key_order_and_map(db, matter=matter)

    provider = make_provider(doc_key_order)
    logger = MatterRunLogger(matter.id, "ingest", logs_dir=logs_dir)
    # Drive the SSE generator to exhaustion (list() consumes every frame).
    list(
        run_phase0(
            db,
            matter=matter,
            user=user,
            storage=storage,
            ocr=FakeOcr(),
            provider=provider,
            on_complete=handle_phase0_completion,
            run_logger=logger,
        )
    )

    # Resolve GM-2's duplicate SUPERSEDED; the ledger (recomputed live in the scorer) then excludes
    # it. An unresolved DUPLICATE_OF is already excluded; SUPERSEDED is the explicit attorney call.
    _resolve_duplicates_superseded(db, user=user, matter=matter)

    # Chronology with narratives OFF: scripted mode mints no narratives, so the zero-unregistered
    # scan runs over empty narratives and must be empty. (Live mode also runs it OFF — the S2
    # number is extraction fidelity, not narrative generation.)
    chronology = build_chronology(db, None, matter=matter, generate_narratives=False)
    return doc_id_by_key, chronology


def _print_report(gold: GoldMatter, report: Tier1Report) -> None:
    """Print a Tier-1 report block — the pasted-into-the-PR M2-exit evidence."""
    print(f"\n=== Tier-1 report [{gold.key}] ===")
    print(f"  encounter_recall      : {report.encounter_recall:.4f}")
    print(f"  encounter_precision   : {report.encounter_precision:.4f}")
    print(f"  dos_provider_accuracy : {report.dos_provider_accuracy:.4f}")
    print(f"  anchor_accuracy       : {report.anchor_accuracy:.4f}")
    print(f"  anchored_rows_ratio   : {report.anchored_rows_ratio:.4f}")
    print(f"  ledger_exact          : {report.ledger_exact}  (delta {report.ledger_delta_cents}c)")
    print(f"  ledger_by_category    : {report.ledger_by_category_exact}")
    print(f"  unregistered_claims   : {list(report.unregistered_claims)}")
    print(f"  duplicate_quarantined : {report.duplicate_quarantined}")
    print(f"  PASSES                : {report.passes()}")


def _assert_m2_exit(gold: GoldMatter, report: Tier1Report) -> None:
    """Assert the M2 exit facts individually with clear messages, then the overall gate."""
    assert report.encounter_recall >= 0.95, (
        f"[{gold.key}] encounter recall {report.encounter_recall:.3f} < 0.95"
    )
    assert report.encounter_precision >= 0.90, (
        f"[{gold.key}] encounter precision {report.encounter_precision:.3f} < 0.90"
    )
    assert report.dos_provider_accuracy >= 0.98, (
        f"[{gold.key}] DOS+provider accuracy {report.dos_provider_accuracy:.3f} < 0.98"
    )
    assert report.anchor_accuracy >= 0.98, (
        f"[{gold.key}] anchor accuracy {report.anchor_accuracy:.3f} < 0.98"
    )
    assert report.anchored_rows_ratio == 1.0, (
        f"[{gold.key}] anchored-rows ratio {report.anchored_rows_ratio:.3f} != 1.0 "
        "(an encounter or billing line has no anchor)"
    )
    assert report.ledger_delta_cents == 0, (
        f"[{gold.key}] ledger delta {report.ledger_delta_cents}c != 0 (does not reconcile)"
    )
    assert report.ledger_by_category_exact, f"[{gold.key}] per-category ledger mismatch"
    assert not report.unregistered_claims, (
        f"[{gold.key}] chronology has unregistered claims: {list(report.unregistered_claims)}"
    )
    assert report.passes(), f"[{gold.key}] Tier-1 report does not pass the M2 exit criterion"


# --------------------------------------------------------------------------------------
# Scripted-mode tests (fast suite)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("build_gold", [build_gm1, build_gm2], ids=["gm1", "gm2"])
def test_scripted_tier1_passes_m2_exit(
    db: Session,
    dev_user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    tmp_path: Path,
    build_gold: Callable[[], GoldMatter],
) -> None:
    gold = build_gold()

    def _make_scripted(doc_key_order: list[str]) -> LLMProvider:
        return scripted_provider_for(gold, doc_key_order)

    doc_id_by_key, chronology = _drive_matter(
        db,
        user=dev_user,
        matter=matter,
        storage=storage,
        gold=gold,
        make_provider=_make_scripted,
        logs_dir=tmp_path,
    )

    report = score_matter(
        db, matter=matter, gold=gold, doc_id_by_key=doc_id_by_key, chronology=chronology
    )
    _print_report(gold, report)
    _assert_m2_exit(gold, report)

    # The pipeline advanced the gate (first run started in corpus_processing).
    db.refresh(matter)
    assert matter.gate_state == GateState.FACTS_REVIEW.value


def test_scripted_gm1_merges_recurring_visits_to_eight(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # GM-1 has 6 pull-1 visits (2 recurring in pull-2) + 2 new pull-2 visits = 8 distinct. The
    # deterministic exact-key merge must collapse the 2 recurrences (10 raw rows -> 8 survivors).
    gold = build_gm1()

    def _make_scripted(doc_key_order: list[str]) -> LLMProvider:
        return scripted_provider_for(gold, doc_key_order)

    _drive_matter(
        db,
        user=dev_user,
        matter=matter,
        storage=storage,
        gold=gold,
        make_provider=_make_scripted,
        logs_dir=tmp_path,
    )

    from app.models.orm import MedicalEncounter

    survivors = list(
        db.execute(
            select(MedicalEncounter).where(MedicalEncounter.matter_id == matter.id)
        ).scalars()
    )
    assert len(survivors) == 8, f"expected 8 merged encounters, got {len(survivors)}"
    # The 2 recurring survivors carry a merged_from snapshot (reversible merge).
    merged = [row for row in survivors if row.merged_from]
    assert len(merged) == 2, f"expected 2 merge survivors, got {len(merged)}"


def test_scripted_gm2_duplicate_quarantined_and_ledger_single_copy(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage, tmp_path: Path
) -> None:
    # GM-2's exact byte-duplicate bill is quarantined DUPLICATE_OF; after resolving it SUPERSEDED
    # the ledger equals the SINGLE-copy total (the dup's lines never sum).
    gold = build_gm2()

    def _make_scripted(doc_key_order: list[str]) -> LLMProvider:
        return scripted_provider_for(gold, doc_key_order)

    doc_id_by_key, chronology = _drive_matter(
        db,
        user=dev_user,
        matter=matter,
        storage=storage,
        gold=gold,
        make_provider=_make_scripted,
        logs_dir=tmp_path,
    )

    # The dup doc is DUPLICATE_OF and its decision resolved SUPERSEDED.
    dup_id = doc_id_by_key["bills_dup"]
    dup_doc = db.get(CaseDocument, dup_id)
    assert dup_doc is not None and dup_doc.dedup_status == DedupStatus.DUPLICATE_OF.value
    decision = db.execute(
        select(DedupDecision).where(DedupDecision.document_id == dup_id)
    ).scalar_one()
    assert decision.resolution == DedupResolution.SUPERSEDED.value

    report = score_matter(
        db, matter=matter, gold=gold, doc_id_by_key=doc_id_by_key, chronology=chronology
    )
    assert report.duplicate_quarantined is True
    assert report.ledger_delta_cents == 0, "ledger must equal the single-copy total"
    assert report.ledger_exact and report.ledger_by_category_exact
    _print_report(gold, report)


# --------------------------------------------------------------------------------------
# Live-mode test (the real S2 datapoint)
# --------------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("build_gold", [build_gm1, build_gm2], ids=["gm1", "gm2"])
def test_live_tier1_passes_m2_exit(
    db: Session,
    dev_user: User,
    matter: Matter,
    storage: LocalDiskStorage,
    tmp_path: Path,
    build_gold: Callable[[], GoldMatter],
) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — live S2 datapoint skipped")

    gold = build_gold()

    # Raise the matter cap so real extraction never trips the default $25 budget.
    budget = db.execute(
        select(MatterBudget).where(MatterBudget.matter_id == matter.id)
    ).scalar_one_or_none()
    if budget is None:
        budget = MatterBudget(
            firm_id=matter.firm_id, matter_id=matter.id, cap_cents=_LIVE_BUDGET_CENTS, spent_cents=0
        )
        tenant_add(db, budget, matter.firm_id)
    else:
        budget.cap_cents = _LIVE_BUDGET_CENTS
    db.commit()

    def _make_live(_doc_key_order: list[str]) -> LLMProvider:
        return AnthropicProvider()

    doc_id_by_key, chronology = _drive_matter(
        db,
        user=dev_user,
        matter=matter,
        storage=storage,
        gold=gold,
        make_provider=_make_live,
        logs_dir=tmp_path,
    )

    report = score_matter(
        db, matter=matter, gold=gold, doc_id_by_key=doc_id_by_key, chronology=chronology
    )
    _print_report(gold, report)

    # Cost + token totals from the LlmCall ledger — the real S2 spend datapoint.
    calls = list(db.execute(select(LlmCall).where(LlmCall.matter_id == matter.id)).scalars())
    total_cost = sum(c.cost_cents for c in calls)
    total_in = sum(c.input_tokens for c in calls)
    total_out = sum(c.output_tokens for c in calls)
    print(f"  [live] calls={len(calls)} cost={total_cost}c in_tok={total_in} out_tok={total_out}")

    _assert_m2_exit(gold, report)
