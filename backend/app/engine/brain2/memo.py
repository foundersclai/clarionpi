"""The strategy memo (Opus) — the attorney-visible framing artifact for a draft.

:func:`generate_memo` produces the demand's strategy memo: an attorney-visible matter artifact
shown at G2.5/G3 and stored on the draft, **never sent to the carrier** (brain2 §Decisions —
hiding the reasoning that shaped the demand would contradict the suite's transparency posture).
It frames; it does not decide valuation or emphasis (those are attorney judgments already made at
G1.5/G2.5).

Two disciplines carry the module:

* **Verbatim attorney signal (input-gate-leverage lesson).** The G1.5
  :class:`~app.models.orm.StrategyInputs` are handed to the model unaltered — never paraphrased —
  alongside the plan's section summary, its emphasis directives, and the registry DISPLAY FORMS of
  the plan's required tokens (so the memo reasons over the fabrication-safe surfaces, inv 5, never
  raw names/amounts).

* **Degrade visibly, never block.** ``client is None`` or an expected offline/budget condition ->
  ``""`` (an empty memo, logged). A second parse failure -> ``""`` too. The memo is stored by the
  caller; an empty memo is the honest "not generated" value, not a stall.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.engine.tokenizer.registry import resolve_for_prompt
from app.models.orm import Matter, StrategyInputs, StrategyPlan
from app.models.schemas import MemoOutput, PlannedSection

_LOG = logging.getLogger("clarionpi.brain2.memo")

# The memo-generation stage id on the metering ledger.
_MEMO_STAGE = "draft.memo"


def _planned_sections(plan: StrategyPlan) -> list[PlannedSection]:
    """The plan's sections as validated :class:`PlannedSection`s (JSON -> typed)."""
    return [PlannedSection.model_validate(s) for s in plan.sections]


def _required_token_display_forms(
    db: Session, *, matter: Matter, sections: list[PlannedSection]
) -> list[str]:
    """The registry display forms of every required token across the plan's sections (deduped).

    Only display forms reach the memo prompt (inv 5) — the memo never sees a raw provider name,
    amount, or citation. First-seen order over the sections keeps the list deterministic.
    """
    seen: set[str] = set()
    forms: list[str] = []
    for section in sections:
        for bare in section.required_tokens:
            full = f"[[{bare}]]"
            if full in seen:
                continue
            seen.add(full)
            forms.append(resolve_for_prompt(db, matter=matter, token=full))
    return forms


def _build_memo_prompt(
    strategy: StrategyInputs | None,
    sections: list[PlannedSection],
    emphasis: list[str],
    required_forms: list[str],
    *,
    insist_json: bool,
) -> str:
    """Assemble the memo prompt: verbatim inputs + plan section summary + display forms.

    The attorney inputs are verbatim (never paraphrased). The section summary lists each section's
    id + purpose. The required-token DISPLAY FORMS (never raw values) give the memo the concrete
    facts it may reference. ``insist_json`` appends the stricter retry suffix.
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
    section_lines = "\n".join(f"- {s.section_id}: {s.purpose}" for s in sections) or "(none)"
    emphasis_block = "\n".join(f"- {d}" for d in emphasis) or "(none)"
    forms_block = "\n".join(f"- {f}" for f in required_forms) or "(none)"
    prompt = (
        "You are writing an internal STRATEGY MEMO for a personal-injury demand package. The memo "
        "is for the attorney and the file — it frames how the demand is built; it is NEVER sent to "
        "the carrier. Do not decide the demand amount or the emphasis (the attorney has already "
        "made those calls); explain the strategy that ties the plan together.\n\n"
        "Attorney strategy inputs (verbatim):\n"
        "---\n"
        f"{inputs_block}\n"
        "---\n\n"
        "Planned demand sections:\n"
        f"{section_lines}\n\n"
        "Emphasis directives:\n"
        f"{emphasis_block}\n\n"
        "Key facts available (reference these by their described form; do not invent others):\n"
        f"{forms_block}\n\n"
        'Return exactly one JSON object and nothing else: {"memo": "<the strategy memo>"}.'
    )
    if insist_json:
        prompt += "\n\nReturn ONLY the JSON object — no prose, no code fences."
    return prompt


def _parse_memo(text: str) -> MemoOutput:
    """Extract the JSON object from a model reply and validate it (house pattern).

    Tolerates surrounding prose / code fences by taking the substring from the first ``{`` to the
    last ``}`` before ``json.loads``. Raises on malformed/absent JSON — the caller turns that into
    the single metered retry.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in memo reply")
    payload = json.loads(text[start : end + 1])
    return MemoOutput.model_validate(payload)


def generate_memo(
    db: Session, client: MeteredLLMClient | None, *, matter: Matter, plan: StrategyPlan
) -> str:
    """Generate the strategy memo for ``plan``. Returns the memo text, or ``""`` on degrade.

    Sees the G1.5 inputs verbatim, the plan's section summary + emphasis directives, and the
    registry display forms of the plan's required tokens (inv 5 — display forms only). Retries ONCE
    on a parse failure (house pattern). ``client is None`` / an offline provider / a budget cap / a
    second parse failure all degrade to ``""`` (logged). The caller stores the result on the draft.
    """
    if client is None:
        _LOG.info("no LLM client for memo generation (matter %s); memo empty", matter.id)
        return ""
    sections = _planned_sections(plan)
    # StrategyInputs is keyed by matter_id (unique), not by its own id — load by matter.
    strategy = db.execute(
        select(StrategyInputs).where(StrategyInputs.matter_id == matter.id)
    ).scalar_one_or_none()
    emphasis = [str(d) for d in plan.emphasis_directives]
    required_forms = _required_token_display_forms(db, matter=matter, sections=sections)

    model = get_settings().memo_model
    try:
        first = client.complete(
            stage=_MEMO_STAGE,
            model=model,
            prompt=_build_memo_prompt(
                strategy, sections, emphasis, required_forms, insist_json=False
            ),
        )
        try:
            parsed = _parse_memo(first.text)
        except (ValueError, json.JSONDecodeError):
            retry = client.complete(
                stage=_MEMO_STAGE,
                model=model,
                prompt=_build_memo_prompt(
                    strategy, sections, emphasis, required_forms, insist_json=True
                ),
            )
            parsed = _parse_memo(retry.text)
    except (ProviderNotConfigured, BudgetExceededError) as exc:
        _LOG.warning("memo generation unavailable (%s); memo empty", type(exc).__name__)
        return ""
    except (ValueError, json.JSONDecodeError) as exc:
        _LOG.warning("memo generation failed to parse twice (%s); memo empty", exc)
        return ""
    return parsed.memo
