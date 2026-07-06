"""Strategy-plan emit — the deterministic section skeleton + the Opus emphasis synthesis.

:func:`emit_strategy_plan` writes a fresh :class:`~app.models.orm.StrategyPlan` row for a matter:
the ordered demand-letter section skeleton from the jurisdiction pack, each section carrying a
**deterministic token allocation** (an attorney edits it at G2.5), plus the demand amount carried
forward from the attorney's G1.5 :class:`~app.models.orm.StrategyInputs` and — when a model is
available — a short list of Opus-synthesized emphasis directives.

Boundaries this module holds:

* **Skeleton is pack-driven, never invented (fail loud).** The section list comes from
  ``load_pack(matter.jurisdiction).letter_sections``; a pack with no ``letter_structure`` block
  raises :class:`~app.rules.errors.LetterStructureMissing` and that propagates — Brain-2 never
  drafts against a made-up skeleton (an invented section set would be unaudited law).

* **The token allocator is deterministic (inv 5).** It queries the matter's latest
  :class:`~app.models.orm.FactToken` rows and hands each section its ``allowed_tokens`` /
  ``required_tokens`` by *bare* id, keyed off the section's ``required_token_kinds`` and a small
  fixed source-ref map. This is a starting allocation the attorney refines at G2.5 — the plan row
  is ``approved=False`` on emit; nothing drafts off it until G2.5-approve (a later wave).

* **Emphasis framing only, no amounts / no law.** The Opus emphasis pass sees the attorney's
  G1.5 inputs verbatim and returns ≤6 one-sentence framing directives. It never proposes an amount
  or a citation (those are attorney judgments / verified-mint territory). A missing/offline model
  degrades to an empty directive list, visibly logged — never a blocked emit.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.core.tenancy import tenant_add
from app.models.enums import TokenKind
from app.models.orm import FactToken, Matter, StrategyInputs, StrategyPlan
from app.models.schemas import PlanEmphasisOutput, PlannedSection

_LOG = logging.getLogger("clarionpi.brain2.plan")

# The emphasis-synthesis stage id on the metering ledger.
_EMPHASIS_STAGE = "plan.emphasis"

# The audit event kind written on a successful emit.
_EMIT_AUDIT_KIND = "strategy_plan_emitted"

# At most this many emphasis directives — a bounded framing list, not a memo.
_MAX_EMPHASIS_DIRECTIVES = 6

# --------------------------------------------------------------------------------------
# Deterministic token allocator (attorney edits the result at G2.5)
# --------------------------------------------------------------------------------------
#
# The allocation is a STARTING point the attorney refines at G2.5 — not a final binding. It keys
# each section off two things: (a) its ``required_token_kinds`` (from the pack skeleton) decide the
# ALLOWED pool (fact sections may use every FACT; amount sections may use every AMT), and (b) a
# small fixed source-ref map decides the REQUIRED subset a well-formed section must carry. All ids
# are BARE (e.g. "FACT_3", "AMT_1") — the bracketed token shape never appears in a plan row.

# Section ids that get NO tokens (client name is matter-level attorney data, not a registry token).
_NO_TOKEN_SECTIONS: frozenset[str] = frozenset({"intro_and_representation"})

# Source-ref prefixes the required-token rules key off (registry source_ref shapes — fact_registry).
_INCIDENT_PREFIX = "incident:"
_ENCOUNTER_PREFIX = "encounter:"
# The two always-on ledger AMT slots (fact_registry / money_engine): the grand billed total and the
# demand-basis total. Their source_refs are ``amt:<ledger key>``.
_AMT_GRAND_BILLED_REF = "amt:specials.grand.billed"
_AMT_DEMAND_BASIS_REF = "amt:specials.demand_basis"

# ``injuries_and_treatment`` requires up to this many of the earliest encounter FACTs.
_MAX_REQUIRED_ENCOUNTERS = 3


def _latest_tokens(db: Session, *, matter: Matter) -> list[FactToken]:
    """Every ``FactToken`` for the matter, latest-version row per ``token_id`` (live fact-slots).

    Mirrors the registry's ``_all_latest_rows`` semantics: a ``token_id`` may have several version
    rows; keep the highest-version one. Deterministic id order out (``token_id``) so allocation is
    stable across emits.
    """
    rows = list(db.execute(select(FactToken).where(FactToken.matter_id == matter.id)).scalars())
    latest: dict[str, FactToken] = {}
    for row in rows:
        seen = latest.get(row.token_id)
        if seen is None or row.registry_version > seen.registry_version:
            latest[row.token_id] = row
    return sorted(latest.values(), key=lambda r: r.token_id)


def _bare_ids(rows: Sequence[FactToken], *, kind: TokenKind) -> list[str]:
    """The bare ``token_id``s of the latest rows of one kind, in ``token_id`` order."""
    return [r.token_id for r in rows if r.kind == kind.value]


def _ordered_encounter_facts(rows: Sequence[FactToken]) -> list[str]:
    """Encounter FACT ids ordered by minted ordinal (earliest slot first).

    The registry mints encounters in ``(created_at, id)`` order at ascending ordinals, so ordering
    by the token's ordinal reproduces the chronological encounter order deterministically.
    """
    encounters = [
        r
        for r in rows
        if r.kind == TokenKind.FACT.value
        and r.source_ref is not None
        and r.source_ref.startswith(_ENCOUNTER_PREFIX)
    ]
    encounters.sort(key=lambda r: _ordinal(r.token_id))
    return [r.token_id for r in encounters]


def _ordinal(token_id: str) -> int:
    """The integer ordinal of a bare ``token_id`` (``"FACT_12" -> 12``)."""
    return int(token_id.rsplit("_", 1)[1])


def _first_by_source_ref(rows: Sequence[FactToken], source_ref: str) -> str | None:
    """The bare id of the latest row whose ``source_ref`` exactly matches, or ``None``."""
    for row in rows:
        if row.source_ref == source_ref:
            return row.token_id
    return None


def _first_by_source_ref_prefix(rows: Sequence[FactToken], prefix: str) -> str | None:
    """The bare id (lowest ordinal) whose ``source_ref`` starts with ``prefix``, or ``None``."""
    matches = [r for r in rows if r.source_ref is not None and r.source_ref.startswith(prefix)]
    if not matches:
        return None
    return min(matches, key=lambda r: _ordinal(r.token_id)).token_id


def _required_for(section_id: str, rows: Sequence[FactToken]) -> list[str]:
    """The deterministic ``required_tokens`` (bare ids) for a section, dedup-preserving order.

    The fixed rules (all ids bare, missing sources simply drop out — the plan stays editable):

    * ``liability`` -> the incident FACT (``source_ref`` prefix ``incident:``) when minted;
    * ``injuries_and_treatment`` -> up to the first 3 encounter FACTs by minted ordinal;
    * ``damages_and_specials`` -> the grand-billed AMT + the demand-basis AMT (when present);
    * ``demand_and_deadline`` -> the demand-basis AMT (when present).

    Every other section requires nothing.
    """
    required: list[str] = []
    if section_id == "liability":
        incident = _first_by_source_ref_prefix(rows, _INCIDENT_PREFIX)
        if incident is not None:
            required.append(incident)
    elif section_id == "injuries_and_treatment":
        required.extend(_ordered_encounter_facts(rows)[:_MAX_REQUIRED_ENCOUNTERS])
    elif section_id == "damages_and_specials":
        grand = _first_by_source_ref(rows, _AMT_GRAND_BILLED_REF)
        if grand is not None:
            required.append(grand)
        demand_basis = _first_by_source_ref(rows, _AMT_DEMAND_BASIS_REF)
        if demand_basis is not None:
            required.append(demand_basis)
    elif section_id == "demand_and_deadline":
        demand_basis = _first_by_source_ref(rows, _AMT_DEMAND_BASIS_REF)
        if demand_basis is not None:
            required.append(demand_basis)
    # dedup preserving first-seen order (defensive — a source_ref could resolve to the same slot).
    seen: set[str] = set()
    out: list[str] = []
    for token_id in required:
        if token_id in seen:
            continue
        seen.add(token_id)
        out.append(token_id)
    return out


def _plan_section(
    section_id: str, purpose: str, max_words: int, kinds: Sequence[str], rows: Sequence[FactToken]
) -> PlannedSection:
    """Build one :class:`PlannedSection` under the deterministic allocation for a skeleton section.

    ``allowed_tokens``: an ``intro_and_representation`` gets ``[]`` (no tokens — client identity is
    matter-level attorney data, not a registry token). Otherwise a section whose
    ``required_token_kinds`` include ``fact`` gets ALL bare FACT ids; one including ``amount`` gets
    ALL bare AMT ids. ``required_tokens`` come from :func:`_required_for`.
    """
    if section_id in _NO_TOKEN_SECTIONS:
        allowed: list[str] = []
    else:
        allowed = []
        if TokenKind.FACT.value in kinds:
            allowed += _bare_ids(rows, kind=TokenKind.FACT)
        if TokenKind.AMOUNT.value in kinds:
            allowed += _bare_ids(rows, kind=TokenKind.AMOUNT)
    return PlannedSection(
        section_id=section_id,
        purpose=purpose,
        allowed_tokens=allowed,
        required_tokens=_required_for(section_id, rows),
        max_words=max_words,
    )


# --------------------------------------------------------------------------------------
# Opus emphasis synthesis (framing only)
# --------------------------------------------------------------------------------------


def _load_strategy(db: Session, *, matter: Matter) -> StrategyInputs | None:
    """The matter's one :class:`StrategyInputs` row, or ``None`` (G1.5 not submitted)."""
    return db.execute(
        select(StrategyInputs).where(StrategyInputs.matter_id == matter.id)
    ).scalar_one_or_none()


def _build_emphasis_prompt(strategy: StrategyInputs | None, *, insist_json: bool) -> str:
    """Assemble the emphasis-synthesis prompt: the G1.5 inputs VERBATIM + the framing instruction.

    The attorney's inputs are handed over unaltered (input-gate-leverage lesson — never paraphrase
    the attorney's signal). The model returns ≤6 one-sentence FRAMING directives: it must not
    propose a dollar amount or a legal citation (those are attorney / verified-mint territory).
    ``insist_json`` appends the stricter retry suffix.
    """
    if strategy is None:
        inputs_block = "(no attorney strategy inputs on file)"
    else:
        inputs_block = (
            f"Liability theory: {strategy.liability_theory}\n"
            f"Injury framing: {strategy.injury_framing}\n"
            f"Emphasis notes: {strategy.emphasis_notes}\n"
            f"Venue posture: {strategy.venue_posture}"
        )
    prompt = (
        "You are helping structure a personal-injury demand letter. Below are the attorney's "
        "strategy inputs, verbatim. Distill them into a short list of EMPHASIS DIRECTIVES that "
        "tell the drafter what to foreground — framing only.\n\n"
        "Attorney strategy inputs:\n"
        "---\n"
        f"{inputs_block}\n"
        "---\n\n"
        f"Rules:\n"
        f"- Return at most {_MAX_EMPHASIS_DIRECTIVES} directives, each ONE sentence.\n"
        "- Framing only: do NOT state a dollar amount or figure, and do NOT cite any case law or "
        "statute.\n"
        "- Do not invent facts; draw only from the inputs above.\n\n"
        "Return exactly one JSON object and nothing else: "
        '{"emphasis_directives": ["<one sentence>", ...]}.'
    )
    if insist_json:
        prompt += "\n\nReturn ONLY the JSON object — no prose, no code fences."
    return prompt


def _parse_emphasis(text: str) -> PlanEmphasisOutput:
    """Extract the JSON object from a model reply and validate it (house pattern).

    Tolerates surrounding prose / code fences by taking the substring from the first ``{`` to the
    last ``}`` before ``json.loads``. Raises on malformed/absent JSON — the caller turns that into
    the single metered retry.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in emphasis reply")
    payload = json.loads(text[start : end + 1])
    return PlanEmphasisOutput.model_validate(payload)


def _synthesize_emphasis(
    client: MeteredLLMClient | None, strategy: StrategyInputs | None
) -> list[str]:
    """Run the Opus emphasis pass, retrying ONCE on a parse failure. Degrades to ``[]``.

    ``client is None`` or an expected-offline provider/budget condition -> ``[]`` (visible degrade,
    logged). A second parse failure -> ``[]`` too (the emphasis is a nice-to-have; the plan still
    emits). Directives are truncated to the ``_MAX_EMPHASIS_DIRECTIVES`` bound defensively.
    """
    if client is None:
        _LOG.info("no LLM client for emphasis synthesis; emitting empty emphasis_directives")
        return []
    model = get_settings().memo_model
    try:
        first = client.complete(
            stage=_EMPHASIS_STAGE,
            model=model,
            prompt=_build_emphasis_prompt(strategy, insist_json=False),
        )
        try:
            parsed = _parse_emphasis(first.text)
        except (ValueError, json.JSONDecodeError):
            retry = client.complete(
                stage=_EMPHASIS_STAGE,
                model=model,
                prompt=_build_emphasis_prompt(strategy, insist_json=True),
            )
            parsed = _parse_emphasis(retry.text)
    except (ProviderNotConfigured, BudgetExceededError) as exc:
        _LOG.warning("emphasis synthesis unavailable (%s); emitting empty", type(exc).__name__)
        return []
    except (ValueError, json.JSONDecodeError) as exc:
        _LOG.warning("emphasis synthesis failed to parse twice (%s); emitting empty", exc)
        return []
    return [str(d) for d in parsed.emphasis_directives[:_MAX_EMPHASIS_DIRECTIVES]]


# --------------------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------------------


def _next_version(db: Session, *, matter: Matter) -> int:
    """One past the count of existing plans for the matter (a plan version is never recycled)."""
    existing = list(
        db.execute(select(StrategyPlan.id).where(StrategyPlan.matter_id == matter.id)).scalars()
    )
    return len(existing) + 1


def emit_strategy_plan(
    db: Session, client: MeteredLLMClient | None, *, matter: Matter
) -> StrategyPlan:
    """Emit a fresh :class:`StrategyPlan` for ``matter`` — skeleton + allocation + emphasis.

    Steps:

    1. Load the pack skeleton (``load_pack(matter.jurisdiction).letter_sections``);
       :class:`~app.rules.errors.LetterStructureMissing` propagates (fail loud — no code-side
       default section set).
    2. Load the matter's latest fact tokens and build one :class:`PlannedSection` per skeleton
       section under the deterministic allocation (see the module doc).
    3. ``demand_amount_cents`` = the attorney's G1.5 ``anchor_amount_cents`` (``None`` allowed — the
       plan is editable at G2.5); ``demand_type = "open"`` (a time-limited demand is a later
       version — D7; the seam exists).
    4. Emphasis: the Opus framing pass (``[]`` when ``client is None`` / degraded, visibly).
    5. Write the row (``version = count+1``, ``registry_version = matter.registry_version``,
       ``approved=False``), audit ``strategy_plan_emitted``, commit, and return.
    """
    # 1. Pack skeleton — LetterStructureMissing propagates (imported lazily so the pack load happens
    # at call time, keeping the module import side-effect-free).
    from app.rules.loader import load_pack

    pack = load_pack(matter.jurisdiction)
    skeleton = pack.letter_sections  # raises LetterStructureMissing when absent

    # 2. Deterministic per-section allocation over the matter's live fact-slots.
    rows = _latest_tokens(db, matter=matter)
    planned = [
        _plan_section(
            s.section_id,
            s.purpose,
            s.max_words,
            [k.value for k in s.required_token_kinds],
            rows,
        )
        for s in skeleton
    ]

    # 3. Demand carried forward from G1.5 (None allowed); demand_type open at v1 (D7 seam).
    strategy = _load_strategy(db, matter=matter)
    demand_amount_cents = strategy.anchor_amount_cents if strategy is not None else None

    # 4. Opus emphasis framing (degrades to []).
    emphasis = _synthesize_emphasis(client, strategy)

    # 5. Persist the row + audit; commit.
    plan = StrategyPlan(
        matter_id=matter.id,
        version=_next_version(db, matter=matter),
        registry_version=matter.registry_version,
        demand_amount_cents=demand_amount_cents,
        demand_type="open",
        sections=[ps.model_dump() for ps in planned],
        emphasis_directives=emphasis,
        approved=False,
    )
    tenant_add(db, plan, matter.firm_id)
    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=None,
        event_kind=_EMIT_AUDIT_KIND,
        payload={
            "matter_id": str(matter.id),
            "version": plan.version,
            "registry_version": plan.registry_version,
            "sections": [ps.section_id for ps in planned],
            "emphasis_directives": len(emphasis),
        },
    )
    db.commit()
    return plan
