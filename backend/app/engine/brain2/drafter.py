"""The per-section drafter (Opus) — layered prompt + the DrafterPromptSnapshot.

:func:`draft_section` drafts ONE tokenized section under its section contract and the matter's
late-bound hard constraints, and persists the :class:`~app.models.orm.DraftSection` row plus its
:class:`DrafterPromptSnapshot` (the judge-symmetry lock the compliance wave re-hashes).

**Layered prompt assembly (the inv-14 port).** The prompt is built from three separately-assembled
layers so a shared rule can never be silently overwritten by matter text:

* ``rules_blocks`` — the lego-block rules: (a) the tokens-only contract ("write using ONLY these
  token placeholders exactly as given …; NEVER write provider names, dates, dollar figures,
  citations, or exhibit numbers as literals"), and (b) the section contract (purpose, ``max_words``,
  the ALLOWED token list with each token's display form, the REQUIRED token list).
* ``matter_directives`` — the matter-fact layer: the attorney's G1.5 inputs verbatim, the plan's
  emphasis directives, and the memo's first 800 chars when present.
* ``final_hard_constraints`` — the late-bound block, rendered by
  :func:`app.engine.brain2.constraints.render_final_hard_constraints` and appended LAST to the user
  prompt so it binds after everything else.

**The snapshot is the judge-symmetry lock.** ``input_hash`` is a sha256 over the canonical JSON of
``[rules_blocks, matter_directives, final_entries, plan.version, plan.registry_version]`` — the
compliance judge receives the same snapshot and re-hashes it, so it grades the exact prompt the
drafter saw (a drift is a hard G3 block). The drafter **never mints** a token (only the registry
mints — the section body is validated downstream and an unknown token is a validator reject).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm_telemetry import MeteredLLMClient
from app.core.tenancy import tenant_add
from app.engine.brain2.constraints import HardConstraintInputs, render_final_hard_constraints
from app.engine.tokenizer.registry import resolve_for_prompt
from app.models.enums import SectionValidation
from app.models.orm import DemandDraft, DraftSection, Matter, StrategyInputs, StrategyPlan
from app.models.schemas import PlannedSection, SectionDraftOutput

_LOG = logging.getLogger("clarionpi.brain2.drafter")

# The drafter stage id on the metering ledger.
_DRAFT_STAGE = "draft.section"

# How much of the memo the matter-directives layer carries (a framing hint, not the whole memo).
_MEMO_PREFIX_CHARS = 800

# The tokens-only contract block — rule (a) of the rules layer. Verbatim (part of the snapshot).
_TOKENS_ONLY_CONTRACT = (
    "Write using ONLY the token placeholders listed below, exactly as given "
    "(for example [[FACT_3]]). NEVER write provider names, dates, dollar figures, citations, "
    "or exhibit numbers as literals — every such fact is carried by a token and will be "
    "rendered from it. Do not invent tokens; use only the tokens listed as allowed."
)


@dataclass(frozen=True)
class DrafterPromptSnapshot:
    """The prompt the drafter saw, plus its hash — the judge-symmetry lock (brain2 §4).

    ``input_hash`` locks drafter↔judge symmetry: the compliance judge re-hashes the same three
    layers + plan version/registry_version and must match. The three layer lists are stored on the
    :class:`~app.models.orm.DraftSection` row (``prompt_snapshot`` JSON) so the judge grades exactly
    what was drafted.
    """

    input_hash: str
    rules_blocks: list[str]
    matter_directives: list[str]
    final_hard_constraints: list[str]

    def to_json(self) -> dict:
        """The JSON persisted on ``DraftSection.prompt_snapshot``."""
        return {
            "input_hash": self.input_hash,
            "rules_blocks": list(self.rules_blocks),
            "matter_directives": list(self.matter_directives),
            "final_hard_constraints": list(self.final_hard_constraints),
        }


@dataclass
class _PromptLayers:
    """The assembled layers for one section draft, pre-hash."""

    rules_blocks: list[str] = field(default_factory=list)
    matter_directives: list[str] = field(default_factory=list)
    final_entries: list[str] = field(default_factory=list)


def _section_contract_block(db: Session, *, matter: Matter, planned: PlannedSection) -> str:
    """Rule (b) of the rules layer: the section contract with each allowed token's display form.

    Lists the purpose, the word ceiling, then the ALLOWED tokens (each with its
    :func:`~app.engine.tokenizer.registry.resolve_for_prompt` display form so the drafter knows what
    the token stands for — inv 5, display forms only), then the REQUIRED tokens. A section with no
    allowed tokens says so explicitly.
    """
    lines = [
        f"Section: {planned.section_id}",
        f"Purpose: {planned.purpose}",
        f"Word limit: {planned.max_words} words maximum.",
    ]
    if planned.allowed_tokens:
        lines.append("Allowed tokens (use only these; each is shown with what it stands for):")
        for bare in planned.allowed_tokens:
            full = f"[[{bare}]]"
            display = resolve_for_prompt(db, matter=matter, token=full)
            lines.append(f"  {full} — {display}")
    else:
        lines.append("Allowed tokens: none — this section uses no tokens at all.")
    if planned.required_tokens:
        required = ", ".join(f"[[{bare}]]" for bare in planned.required_tokens)
        lines.append(f"Required tokens (each MUST appear at least once): {required}")
    else:
        lines.append("Required tokens: none.")
    return "\n".join(lines)


def _matter_directives(
    strategy: StrategyInputs | None, plan: StrategyPlan, draft: DemandDraft
) -> list[str]:
    """The matter-fact layer: attorney inputs verbatim + emphasis directives + memo prefix.

    Each is a separate directive string (kept apart from the rules layer so matter text can never
    overwrite a rule). The attorney inputs are verbatim; the memo is truncated to a framing prefix.
    Empty pieces are omitted.
    """
    directives: list[str] = []
    if strategy is not None:
        directives.append(
            "Attorney strategy inputs (verbatim):\n"
            f"Liability theory: {strategy.liability_theory}\n"
            f"Injury framing: {strategy.injury_framing}\n"
            f"Emphasis notes: {strategy.emphasis_notes}\n"
            f"Venue posture: {strategy.venue_posture}"
        )
    for directive in plan.emphasis_directives:
        directives.append(f"Emphasis: {directive}")
    memo = (draft.memo or "").strip()
    if memo:
        directives.append(f"Strategy memo (context):\n{memo[:_MEMO_PREFIX_CHARS]}")
    return directives


def _compute_hash(layers: _PromptLayers, plan: StrategyPlan) -> str:
    """sha256 over the canonical JSON of the three layers + plan version + registry version.

    ``sort_keys=True`` + compact separators make the digest stable for identical inputs and change
    when ANY layer or the plan version/registry version changes — the judge-symmetry contract.
    """
    payload = {
        "rules_blocks": layers.rules_blocks,
        "matter_directives": layers.matter_directives,
        "final_hard_constraints": layers.final_entries,
        "plan_version": plan.version,
        "plan_registry_version": plan.registry_version,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _build_user_prompt(layers: _PromptLayers, *, retry_violations: list[str] | None) -> str:
    """Assemble the drafter's user prompt from the layers, final constraints appended LAST.

    Order: the rules blocks, then the matter directives, then the JSON-output instruction, then the
    late-bound ``final_hard_constraints`` block (appended last so it binds after everything). On a
    retry the violations from the first attempt are appended at the very tail (after the final
    constraints) so the model sees exactly what to fix — the single retry is the caller's; this only
    renders it.
    """
    parts: list[str] = []
    parts.extend(layers.rules_blocks)
    if layers.matter_directives:
        parts.append("\n".join(layers.matter_directives))
    parts.append(
        'Return exactly one JSON object and nothing else: {"body_tokenized": "<the section '
        'prose, using only the allowed tokens>"}.'
    )
    prompt = "\n\n".join(parts)
    # Late-bound hard constraints — appended LAST (binds after the rest of the prompt).
    prompt += render_final_hard_constraints(layers.final_entries)
    if retry_violations:
        joined = "\n".join(f"- {v}" for v in retry_violations)
        prompt += (
            "\n\n---\nYour previous attempt was rejected for these reasons:\n"
            f"{joined}\n"
            "Fix every one. Return ONLY the JSON object; use only the allowed tokens; write no "
            "provider names, dates, dollar figures, or citations as literals."
        )
    return prompt


def _parse_section(text: str) -> SectionDraftOutput:
    """Extract the JSON object from a model reply and validate it (house pattern).

    Tolerates surrounding prose / code fences by taking the substring from the first ``{`` to the
    last ``}`` before ``json.loads``. Raises on malformed/absent JSON or an empty body — the caller
    turns that into the single metered retry.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in section-draft reply")
    payload = json.loads(text[start : end + 1])
    return SectionDraftOutput.model_validate(payload)


def build_snapshot(
    db: Session,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    planned: PlannedSection,
    constraints: HardConstraintInputs,
) -> DrafterPromptSnapshot:
    """Assemble the three prompt layers and compute the snapshot hash for a section.

    Pure of the model call — this is the exact input the judge re-hashes. Exposed so the compliance
    wave can rebuild the snapshot from the same surfaces and compare ``input_hash`` (the symmetry
    lock). ``rules_blocks`` = [tokens-only contract, section contract]; ``matter_directives`` = the
    matter-fact layer; ``final_hard_constraints`` = ``constraints.to_entries()``.
    """
    strategy = _load_strategy(db, matter=matter)
    layers = _PromptLayers(
        rules_blocks=[
            _TOKENS_ONLY_CONTRACT,
            _section_contract_block(db, matter=matter, planned=planned),
        ],
        matter_directives=_matter_directives(strategy, plan, draft),
        final_entries=constraints.to_entries(),
    )
    return DrafterPromptSnapshot(
        input_hash=_compute_hash(layers, plan),
        rules_blocks=layers.rules_blocks,
        matter_directives=layers.matter_directives,
        final_hard_constraints=layers.final_entries,
    )


def _load_strategy(db: Session, *, matter: Matter) -> StrategyInputs | None:
    """The matter's one :class:`StrategyInputs` row, or ``None``."""
    from sqlalchemy import select

    return db.execute(
        select(StrategyInputs).where(StrategyInputs.matter_id == matter.id)
    ).scalar_one_or_none()


def draft_section(
    db: Session,
    client: MeteredLLMClient,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    planned: PlannedSection,
    constraints: HardConstraintInputs,
    sort_order: int,
    retry_violations: list[str] | None = None,
) -> DraftSection:
    """Draft one tokenized section and persist its :class:`~app.models.orm.DraftSection` row.

    Builds the layered prompt (rules-blocks, matter-directives, late-bound final constraints — the
    constraints appended LAST), calls the metered client (stage ``draft.section``, model
    ``settings.drafter_model``), parses the ``{"body_tokenized": ...}`` output (house retry-once on
    a parse miss handled here so a malformed reply does not surface as a run failure), and creates
    the row: ``draft_id``, ``section_id``, ``purpose``, ``sort_order``, ``registry_version =
    plan.registry_version``, ``validation = RETRY_PENDING`` (deterministic validation has not run
    yet — the caller runs it), the parsed body, and the ``prompt_snapshot`` JSON.

    ``retry_violations`` (the caller's single content retry) is appended to the prompt tail so the
    model is told what to fix; it does not change the snapshot layers — the snapshot always reflects
    the section contract + directives + constraints, which are what the judge grades.

    Provider / budget errors are NOT caught here — they belong to the run's budget-stop path.
    """
    snapshot = build_snapshot(
        db, matter=matter, plan=plan, draft=draft, planned=planned, constraints=constraints
    )
    layers = _PromptLayers(
        rules_blocks=snapshot.rules_blocks,
        matter_directives=snapshot.matter_directives,
        final_entries=snapshot.final_hard_constraints,
    )
    model = get_settings().drafter_model

    body = _draft_body(client, layers, model=model, retry_violations=retry_violations)

    section = DraftSection(
        draft_id=draft.id,
        section_id=planned.section_id,
        purpose=planned.purpose,
        body_tokenized=body,
        registry_version=plan.registry_version,
        validation=SectionValidation.RETRY_PENDING.value,
        sort_order=sort_order,
        prompt_snapshot=snapshot.to_json(),
    )
    tenant_add(db, section, matter.firm_id)
    db.flush()
    return section


def _draft_body(
    client: MeteredLLMClient,
    layers: _PromptLayers,
    *,
    model: str,
    retry_violations: list[str] | None,
) -> str:
    """Run the metered draft call, retrying ONCE on a parse miss. Returns the tokenized body.

    The parse retry (a malformed reply) is the house one-retry, local to this call so a bad reply
    costs exactly one extra metered call and never surfaces as a run failure. This is distinct from
    the caller's CONTENT retry (validation violations) — ``retry_violations``, when set, is the
    caller re-drafting after a validation failure and is rendered into the prompt tail here.
    """
    prompt = _build_user_prompt(layers, retry_violations=retry_violations)
    first = client.complete(stage=_DRAFT_STAGE, model=model, prompt=prompt)
    try:
        return _parse_section(first.text).body_tokenized
    except (ValueError, json.JSONDecodeError) as exc:
        _LOG.warning("section-draft parse failed (%s); one stricter retry", exc)
    retry_prompt = _build_user_prompt(layers, retry_violations=retry_violations) + (
        "\n\nReturn ONLY the JSON object — no prose, no code fences."
    )
    retry = client.complete(stage=_DRAFT_STAGE, model=model, prompt=retry_prompt)
    return _parse_section(retry.text).body_tokenized
