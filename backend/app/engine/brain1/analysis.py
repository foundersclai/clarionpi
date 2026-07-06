"""The Brain-1 analysis run — the ``analysis_running -> evidence_review`` build, streamed over SSE.

This module is the composition point that turns the G1.5-approved matter into the payload G2a
reviews. It runs *after* the attorney submits strategy intake (the ``STRATEGY_INTAKE ->
ANALYSIS_RUNNING`` edge) and, on completion, advances the matter to ``EVIDENCE_REVIEW`` through the
sole sanctioned door (:func:`~app.engine.orchestrator.machine.advance`). It composes the already
landed Brain-1 surfaces — it invents no analysis of its own:

* :func:`~app.engine.tokenizer.registry.sync_extracted_facts` — an idempotent registry re-sync that
  catches any encounter merges committed after Phase 0 (a late-document run folds new facts in but
  leaves the analysis re-run to *this* module).
* :func:`~app.engine.brain1.chronology.build_chronology` — the derived chronology + per-encounter
  tokens-only narratives (metered; degrades visibly when the provider is offline).
* the money engine (:func:`~app.money.assemble.compute_matter_ledger` ->
  :func:`~app.money.specials.amounts_for_registry` ->
  :func:`~app.engine.tokenizer.registry.mint_amounts`) — the specials ledger + its ``[[AMT]]`` mint.
* :func:`~app.engine.brain1.risk.run_risk_detectors` — the anchored risk flags G2a dispositions.

Discipline mirrors :mod:`app.corpus.ingest.phase0`: one SSE frame per step (``format_sse`` +
:class:`~app.models.enums.SseEvent` only), per-step commits so a mid-run failure keeps the
completed work, a per-matter run log (invariant 14; phase ``"analysis"``), and a single ERROR
frame — never a raw traceback — when an *unexpected* exception escapes the composed stages. The
run is **re-entrant**: a re-POST after a partial failure resumes and finishes, because every stage
it composes is itself idempotent (registry sync de-dupes, chronology never regenerates a non-empty
narrative, the risk re-run preserves dispositioned flags). The gate step advances only from
``ANALYSIS_RUNNING`` — a re-POST that already reached ``EVIDENCE_REVIEW`` is a no-op at the gate
(the route owns the ``EVIDENCE_REVIEW -> ANALYSIS_RUNNING`` re-run edge before it calls this).

The client is ALWAYS constructed and passed to the composed stages (never ``None``): each stage
owns its own offline degradation. With the ``null`` provider, chronology skips narratives and the
risk engine skips its LLM labeling pass — both *visibly* (counted in the summary), and the
deterministic risk detectors + the ledger mint (no model on their path) still run and still advance
the gate. This keeps the no-LLM path a runnable, honest degrade rather than a stall.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.api.sse_utils import format_sse
from app.core.audit import record_event
from app.core.llm_provider import LLMProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_logs import MatterRunLogger
from app.engine.brain1.chronology import build_chronology
from app.engine.brain1.risk import run_risk_detectors
from app.engine.orchestrator.machine import advance
from app.engine.tokenizer import registry
from app.models.enums import GateEvent, GateState, SseEvent
from app.models.orm import Matter, User
from app.money.assemble import compute_matter_ledger
from app.money.specials import amounts_for_registry
from app.rules.errors import UnsupportedJurisdiction
from app.rules.loader import load_pack

_LOG = logging.getLogger("clarionpi.analysis")

# Truncate an unexpected error's detail so one runaway repr can't flood the SSE frame (mirrors
# phase0's discipline).
_ERROR_DETAIL_MAX = 300

# The run-log phase name (one file per matter/phase — invariant 14).
_PHASE = "analysis"


@dataclass(frozen=True)
class AnalysisSummary:
    """Roll-up counters for one analysis run — the shape of the final ``completed`` STATUS frame.

    ``gate_advanced`` is ``True`` only when this run moved the matter out of ``analysis_running``
    into ``evidence_review``. ``flags_llm_skipped`` marks that the risk labeling pass did not run
    (offline provider / no budget) — the deterministic flags still stand. ``registry_version`` is
    the version after the sync + AMT mint (the derived facts G2a confirms against).
    """

    chronology_rows: int
    narratives_generated: int
    narratives_skipped: int
    unregistered_claims: int
    overlay_conflicts: int
    ledger_grand_billed_cents: int
    amounts_minted: int
    facts_synced: int
    flags_deterministic: int
    flags_llm: int
    flags_llm_skipped: bool
    registry_version: int
    gate_advanced: bool


def run_analysis(
    db: Session,
    *,
    matter: Matter,
    user: User,
    provider: LLMProvider,
    run_logger: MatterRunLogger | None = None,
) -> Iterator[str]:
    """Run the Brain-1 analysis for ``matter``, yielding SSE frames (strings from ``format_sse``).

    Composes the landed Brain-1 stages in order — registry sync, chronology, ledger AMT mint, risk
    flags — then advances the gate (``ANALYSIS_RUNNING -> EVIDENCE_REVIEW``). See the module doc for
    the invariants each step upholds. Re-entrant: per-step commits + idempotent stages mean a
    re-POST after a partial failure resumes and completes. Never raises through the stream — an
    unexpected exception ends it with a single ERROR frame after logging.
    """
    logger = run_logger if run_logger is not None else MatterRunLogger(matter.id, _PHASE)

    # The metered door for every model call this run makes (invariant 12). Constructed ALWAYS and
    # handed to each composed stage — the stages own their offline degradation (see module doc).
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)

    try:
        logger.log("run_started", gate_state=matter.gate_state)
        yield format_sse(
            SseEvent.STATUS,
            {"phase": _PHASE, "state": "started", "matter_id": str(matter.id)},
        )

        # ---- Step 1: registry sync (idempotent — catches post-phase0 encounter merges) -------
        yield format_sse(
            SseEvent.STATUS,
            {"phase": _PHASE, "state": "step", "step": "registry_sync"},
        )
        facts_sync = registry.sync_extracted_facts(db, matter=matter)
        facts_synced = facts_sync.minted
        registry_version = facts_sync.version
        logger.log("registry_synced", **asdict(facts_sync))

        # ---- Step 2: chronology (derived rows + tokens-only narratives; metered) --------------
        yield format_sse(
            SseEvent.STATUS,
            {"phase": _PHASE, "state": "step", "step": "chronology"},
        )
        chronology = build_chronology(db, client, matter=matter, generate_narratives=True)
        # unregistered_claims MUST be empty on a healthy build; a non-empty set is a G3 block
        # downstream, so we log it loud here and continue (the build itself did not raise).
        if chronology.unregistered_claims:
            _LOG.error(
                "analysis chronology has %d unregistered claim(s) for matter %s: %s",
                len(chronology.unregistered_claims),
                matter.id,
                ", ".join(chronology.unregistered_claims),
            )
        logger.log(
            "chronology_built",
            rows=len(chronology.rows),
            narratives_generated=chronology.narratives_generated,
            narratives_skipped=chronology.narratives_skipped,
            narratives_failed=chronology.narratives_failed,
            overlays_conflict=chronology.overlays_conflict,
            overlays_parked=chronology.overlays_parked,
            unregistered_claims=len(chronology.unregistered_claims),
        )

        # ---- Step 3: ledger AMT mint (no model on this path) ----------------------------------
        yield format_sse(
            SseEvent.STATUS,
            {"phase": _PHASE, "state": "step", "step": "ledger"},
        )
        ledger_grand_billed_cents = 0
        amounts_minted = 0
        # Matter creation already gated the jurisdiction, so an UnsupportedJurisdiction here is
        # defensive: log + skip the ledger, never crash the run.
        try:
            pack = load_pack(matter.jurisdiction)
        except UnsupportedJurisdiction:
            logger.log("ledger_skipped", reason="jurisdiction_unsupported")
        else:
            ledger = compute_matter_ledger(db, matter=matter, pack=pack)
            ledger_grand_billed_cents = ledger.grand_total.billed_cents
            amounts = amounts_for_registry(ledger)
            amt_sync = registry.mint_amounts(db, matter=matter, amounts=amounts)
            amounts_minted = amt_sync.minted
            registry_version = amt_sync.version
            logger.log(
                "ledger_amounts_minted",
                count=amt_sync.minted,
                line_set_hash=ledger.line_set_hash,
                demand_basis_total_cents=ledger.demand_basis_total_cents,
                basis=ledger.basis,
            )

        # ---- Step 4: risk flags (deterministic + LLM labeling; idempotent re-run) -------------
        yield format_sse(
            SseEvent.STATUS,
            {"phase": _PHASE, "state": "step", "step": "risk_flags"},
        )
        # Always pass the client: the engine runs the deterministic detectors regardless and skips
        # the LLM labeling pass on its own when the provider is offline (ProviderNotConfigured is
        # handled inside run_risk_detectors -> llm_skipped, never surfaced here).
        risk = run_risk_detectors(db, client, matter=matter)
        logger.log(
            "risk_flags_generated",
            deterministic_flags=risk.deterministic_flags,
            llm_flags=risk.llm_flags,
            anchors_rejected=risk.anchors_rejected,
            llm_skipped=risk.llm_skipped,
            preserved_dispositioned=risk.preserved_dispositioned,
            replaced_open=risk.replaced_open,
        )

        # ---- Gate step: advance ANALYSIS_RUNNING -> EVIDENCE_REVIEW (guardless) ----------------
        # ``gate_advanced`` is known before the summary is built, so the audit + completed frame
        # carry the truthful value.
        gate_advanced = matter.gate_state == GateState.ANALYSIS_RUNNING.value
        summary = AnalysisSummary(
            chronology_rows=len(chronology.rows),
            narratives_generated=chronology.narratives_generated,
            narratives_skipped=chronology.narratives_skipped,
            unregistered_claims=len(chronology.unregistered_claims),
            overlay_conflicts=chronology.overlays_conflict,
            ledger_grand_billed_cents=ledger_grand_billed_cents,
            amounts_minted=amounts_minted,
            facts_synced=facts_synced,
            flags_deterministic=risk.deterministic_flags,
            flags_llm=risk.llm_flags,
            flags_llm_skipped=risk.llm_skipped,
            registry_version=registry_version,
            gate_advanced=gate_advanced,
        )

        if gate_advanced:
            transition = advance(GateState.ANALYSIS_RUNNING, GateEvent.ANALYSIS_COMPLETE)
            matter.gate_state = transition.to.value
            record_event(
                db,
                firm_id=matter.firm_id,
                actor_id=user.id,
                event_kind="analysis_completed",
                payload={"matter_id": str(matter.id), **asdict(summary)},
            )
            db.commit()
            logger.log(
                "gate_advanced",
                **{"from": GateState.ANALYSIS_RUNNING.value, "to": transition.to.value},
            )
            yield format_sse(
                SseEvent.GATE_READY,
                {"gate": "evidence_review", "matter_id": str(matter.id)},
            )
        else:
            # A re-POST that already reached evidence_review (the route owns the re-run edge before
            # calling here): the derived work above was re-computed idempotently; the gate stays.
            db.commit()
            logger.log("gate_unchanged", gate_state=matter.gate_state)

        logger.log("run_completed", **asdict(summary))
        yield format_sse(
            SseEvent.STATUS,
            {"phase": _PHASE, "state": "completed", **asdict(summary)},
        )
    except Exception as exc:
        # The composed stages absorb every EXPECTED offline condition themselves (chronology skips
        # narratives, the risk engine skips its LLM pass, the ledger skips on an unsupported
        # jurisdiction). Reaching here means something genuinely unexpected broke. We do NOT
        # re-raise through the stream: per-step commits already landed and the run is re-entrant, so
        # a re-POST resumes and finishes. Emit one ERROR frame and end cleanly.
        logger.log("run_error", error=type(exc).__name__)
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": _PHASE,
                "error": type(exc).__name__,
                "detail": str(exc)[:_ERROR_DETAIL_MAX],
            },
        )
        return
