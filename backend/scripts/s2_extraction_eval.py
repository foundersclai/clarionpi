#!/usr/bin/env python3
"""S2 extraction-fidelity spike runner (protocol: backlog/pi/11_spike_briefs.md §3).

A thin CLI over the SAME pieces the Tier-1 test suite composes: it builds a THROWAWAY in-memory
DB + tmp storage, drives a gold matter through the whole Phase-0 pipeline (classify → pages → dedup
→ extract → merge → registry sync → ledger AMT mint) plus a chronology build, scores it with
:func:`tests.evals.tier1.score_matter`, and prints the :class:`~tests.evals.tier1.Tier1Report` as a
RESULTS.md markdown row (plus the ``PROMPT_VERSIONS`` and the LlmCall cost). Exit code is 0 iff the
report passes the M2 exit criterion.

Two providers, one gold, one scorer:

* ``--provider null`` (default) — the deterministic ``ScriptedProvider`` "perfect-ish" extractor
  (:func:`tests.evals.gold_fixtures.scripted_provider_for`). No network; proves the plumbing.
* ``--provider anthropic`` — a live :class:`~app.core.llm_provider.AnthropicProvider` (needs
  ``ANTHROPIC_API_KEY``); the real S2 datapoint. The ``--rounds-note`` free text is echoed into the
  output so each prompt-iteration round's context is attributable (the §3 ≤3-round protocol).

Import mechanism (mirrors ``backend/tests/scripts/test_s1_scorer.py``): ``backend/scripts/`` is not
a package and is not on ``sys.path`` when run as ``.venv/bin/python scripts/s2_extraction_eval.py``
from ``backend/`` (Python puts ``scripts/`` on ``sys.path[0]``, not ``backend/``). So this file
prepends the repo's ``backend/`` dir (its own parent) to ``sys.path`` before importing ``app.*`` /
``tests.evals.*``.

Usage (from ``backend/``)::

    .venv/bin/python scripts/s2_extraction_eval.py --matter both --provider null
    ANTHROPIC_API_KEY=... .venv/bin/python scripts/s2_extraction_eval.py \\
        --matter both --provider anthropic --rounds-note "round 1 — baseline prompts"
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

# --- Path bootstrap: make app.* / tests.* importable when run as a bare script (see docstring). ---
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# In-memory SQLite + tmp roots: pin APP_ENV=test BEFORE importing app config so get_settings picks
# the in-memory database and a tempdir storage root (never the repo tree). A live provider is
# constructed directly, so LLM_PROVIDER need not be set here.
os.environ.setdefault("APP_ENV", "test")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import seed_dev_firm_and_user  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory  # noqa: E402
from app.core.llm_provider import AnthropicProvider, LLMProvider  # noqa: E402
from app.core.matter_logs import MatterRunLogger  # noqa: E402
from app.core.storage import LocalDiskStorage  # noqa: E402
from app.core.tenancy import tenant_add  # noqa: E402
from app.corpus.extraction.prompts import PROMPT_VERSIONS  # noqa: E402
from app.corpus.ingest.dedup import resolve_dedup_decision  # noqa: E402
from app.corpus.ingest.phase0 import run_phase0  # noqa: E402
from app.corpus.ocr import FakeOcr  # noqa: E402
from app.engine.brain1.chronology import build_chronology  # noqa: E402
from app.models.enums import (  # noqa: E402
    DedupResolution,
    DedupStatus,
    DocStatus,
    DocType,
    GateState,
)
from app.models.orm import (  # noqa: E402
    CaseDocument,
    DedupDecision,
    LlmCall,
    Matter,
    MatterBudget,
    User,
)
from tests.evals.gold_fixtures import (  # noqa: E402
    GoldMatter,
    build_gm1,
    build_gm2,
    scripted_provider_for,
)
from tests.evals.tier1 import Tier1Report, score_matter  # noqa: E402

_LIVE_BUDGET_CENTS = 500_00
_GOLD_BUILDERS = {"gm1": build_gm1, "gm2": build_gm2}


def _fresh_session_and_storage(tmp_root: Path) -> tuple[Session, LocalDiskStorage]:
    """A throwaway in-memory engine + open session + tmp-dir storage for one CLI run."""
    engine = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=_LIVE_BUDGET_CENTS,
        )
    )
    create_all_for_tests(engine)
    session = create_session_factory(engine)()
    storage = LocalDiskStorage(tmp_root / "storage")
    return session, storage


def _make_matter(session: Session, user: User) -> Matter:
    """An AZ mva matter sitting in corpus_processing (the Phase-0 entry state)."""
    matter = Matter(
        client_display_name="S2 Gold Client",
        claim_type="mva",
        incident_date=date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    tenant_add(session, matter, user.firm_id)
    session.commit()
    # A generous cap so a live run never trips the default budget.
    budget = MatterBudget(
        firm_id=matter.firm_id, matter_id=matter.id, cap_cents=_LIVE_BUDGET_CENTS, spent_cents=0
    )
    tenant_add(session, budget, matter.firm_id)
    session.commit()
    return matter


def _upload_gold_docs(
    session: Session, *, user: User, matter: Matter, storage: LocalDiskStorage, gold: GoldMatter
) -> None:
    """Store each gold PDF + create an UPLOADED doc_type=OTHER doc keyed (filename) by gold key.

    Docs get strictly-increasing (second-spaced) ``created_at`` in the gold's dict order so the
    ``(created_at, id)`` order dedup + the runner use is DETERMINISTIC — dedup reliably flags the
    later byte-copy (``bills_dup``) as ``DUPLICATE_OF``, not whichever UUID sorted first.
    """
    base_ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
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
            created_at=base_ts + timedelta(seconds=index),
        )
        tenant_add(session, doc, user.firm_id)
    session.commit()


def _doc_key_order_and_map(
    session: Session, *, matter: Matter
) -> tuple[list[str], dict[str, uuid.UUID]]:
    """Fixture-doc keys in (created_at, id) order + the key->document_id map (see the test)."""
    docs = list(
        session.execute(
            select(CaseDocument)
            .where(CaseDocument.matter_id == matter.id)
            .order_by(CaseDocument.created_at, CaseDocument.id)
        ).scalars()
    )
    return [d.filename for d in docs], {d.filename: d.id for d in docs}


def _resolve_dups_superseded(session: Session, *, user: User, matter: Matter) -> None:
    """Resolve every PENDING DUPLICATE_OF decision SUPERSEDED (GM-2's exact-copy bill)."""
    decisions = list(
        session.execute(
            select(DedupDecision).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.status == DedupStatus.DUPLICATE_OF.value,
                DedupDecision.resolution == DedupResolution.PENDING.value,
            )
        ).scalars()
    )
    for decision in decisions:
        resolve_dedup_decision(
            session, user=user, decision=decision, resolution=DedupResolution.SUPERSEDED
        )


def _run_one(
    *, gold: GoldMatter, provider_kind: str, tmp_root: Path
) -> tuple[Tier1Report, int, str]:
    """Drive one gold matter end-to-end and score it. Returns (report, cost_cents, model).

    ``provider_kind`` is ``"null"`` (scripted) or ``"anthropic"`` (live). Uses a fresh throwaway DB
    per matter so runs never cross-contaminate.
    """
    session, storage = _fresh_session_and_storage(tmp_root)
    try:
        user = seed_dev_firm_and_user(session)
        matter = _make_matter(session, user)
        _upload_gold_docs(session, user=user, matter=matter, storage=storage, gold=gold)
        doc_key_order, doc_id_by_key = _doc_key_order_and_map(session, matter=matter)

        provider: LLMProvider
        if provider_kind == "anthropic":
            provider = AnthropicProvider()
        else:
            provider = scripted_provider_for(gold, doc_key_order)

        logger = MatterRunLogger(matter.id, "ingest", logs_dir=tmp_root / "logs")
        list(
            run_phase0(
                session,
                matter=matter,
                user=user,
                storage=storage,
                ocr=FakeOcr(),
                provider=provider,
                run_logger=logger,
            )
        )
        _resolve_dups_superseded(session, user=user, matter=matter)
        chronology = build_chronology(session, None, matter=matter, generate_narratives=False)
        report = score_matter(
            session, matter=matter, gold=gold, doc_id_by_key=doc_id_by_key, chronology=chronology
        )

        calls = list(
            session.execute(select(LlmCall).where(LlmCall.matter_id == matter.id)).scalars()
        )
        cost_cents = sum(c.cost_cents for c in calls)
        model = get_settings().extractor_model
        return report, cost_cents, model
    finally:
        session.close()


def _results_table(rows: list[str]) -> str:
    """The RESULTS.md-shaped markdown table (header matches spikes/s2_extraction_fidelity)."""
    header = (
        "| matter | prompt_version | provider/model | recall | precision | "
        "dos+prov | anchor | ledger exact | unregistered | result | cost (c) |"
    )
    divider = "|---|---|---|---|---|---|---|---|---|---|---|"
    return "\n".join([header, divider, *rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the S2 extraction-fidelity spike over the gold matters and print a RESULTS.md "
            "row per matter. Exit 0 iff every scored matter passes the M2 exit criterion."
        )
    )
    parser.add_argument("--matter", choices=["gm1", "gm2", "both"], default="both")
    parser.add_argument("--provider", choices=["null", "anthropic"], default="null")
    parser.add_argument(
        "--rounds-note",
        default="",
        help="free-text note for the prompt-iteration round (echoed into the output, §3 protocol)",
    )
    args = parser.parse_args(argv)

    if args.provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("--provider anthropic requires ANTHROPIC_API_KEY (unset or blank)")

    matter_keys = ["gm1", "gm2"] if args.matter == "both" else [args.matter]

    print(f"# S2 extraction-fidelity run — provider={args.provider}")
    print(f"prompt_versions: {dict(PROMPT_VERSIONS)}")
    if args.rounds_note:
        print(f"rounds-note: {args.rounds_note}")
    print()

    rows: list[str] = []
    all_pass = True
    with TemporaryDirectory(prefix="s2-eval-") as tmp:
        tmp_root = Path(tmp)
        for key in matter_keys:
            gold = _GOLD_BUILDERS[key]()
            report, cost_cents, model = _run_one(
                gold=gold, provider_kind=args.provider, tmp_root=tmp_root / key
            )
            prompt_version = ",".join(f"{k}={v}" for k, v in sorted(PROMPT_VERSIONS.items()))
            provider_model = f"{args.provider}/{model}"
            rows.append(
                report.as_markdown_row(
                    label=key,
                    prompt_version=prompt_version,
                    model=provider_model,
                    cost_cents=cost_cents,
                )
            )
            all_pass = all_pass and report.passes()

    print(_results_table(rows))
    print()
    if all_pass:
        print("PASS")
    else:
        print("FAIL — see the row(s) above; route to the §3 rescope if <90% recall after round 3")
    return 0 if all_pass else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
