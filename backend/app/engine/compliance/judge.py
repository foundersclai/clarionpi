"""The semantic G3 judge (Sonnet) — snapshot symmetry (inv 13).

:func:`run_judge` grades a rendered draft's sections for the THREE semantic check kinds
(``unsupported_causation`` / ``strategy_drift`` / ``tone``) that no code predicate can decide. It
is the counterpart to the deterministic checks: code owns mechanical verdicts, the judge owns
semantic ones, and neither side post-filters the other (inv 13).

**Snapshot symmetry is the load-bearing contract.** Before grading a section the judge rebuilds
the drafter's :class:`~app.engine.brain2.drafter.DrafterPromptSnapshot` from the same surfaces the
drafter used (freshly-built hard constraints + the section's planned contract) and compares its
``input_hash`` to the one persisted on the section. A mismatch means the world changed since
drafting (constraints / plan drifted), so the judge would be grading a DIFFERENT prompt than the
drafter saw — that is a :class:`SnapshotDrift`, raised loudly (never graded through). The judge is
then handed the *persisted* snapshot blocks VERBATIM (rules_blocks + matter_directives +
final_hard_constraints) plus the section's rendered preview, so it grades exactly the drafted
world.

**Fail-visible, never fail-silent.** The judge reply is parsed as a
:class:`~app.models.schemas.JudgeFindingBatch` — whose schema itself rejects a mechanical
``check_kind`` (inv 13: a judge claiming an orphan/AMT/etc. fails validation). A parse/validation
miss gets ONE stricter retry; a SECOND failure does NOT pass the section as clean — it emits one
BLOCKING ``tone`` finding ("semantic judge failed to return a valid verdict … manual review
required") that the attorney dispositions. (The ``tone`` kind is used deliberately: it is the
generic semantic kind, so the fail-visible marker rides the semantic bucket without inventing a
new check kind — commented at the emission site.) A provider/budget outage is a typed
:class:`JudgeUnavailable` the engine decides on (the deterministic findings still stand; G3 shows
the semantic pass as unavailable).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.engine.brain2.constraints import HardConstraintInputs, build_hard_constraints
from app.engine.brain2.drafter import build_snapshot
from app.models.enums import CheckKind
from app.models.orm import ComplianceFinding, DemandDraft, DraftSection, Matter, StrategyPlan
from app.models.schemas import JudgeFindingBatch, PlannedSection

_LOG = logging.getLogger("clarionpi.compliance.judge")

# The judge stage id on the metering ledger.
_JUDGE_STAGE = "compliance.judge"

# The three semantic kinds the judge may flag (mirrors schemas._SEMANTIC_CHECK_KINDS; the schema
# enforces it, this is the instruction copy).
_SEMANTIC_KINDS = (
    CheckKind.UNSUPPORTED_CAUSATION.value,
    CheckKind.STRATEGY_DRIFT.value,
    CheckKind.TONE.value,
)


class SnapshotDrift(Exception):
    """The judge's rebuilt snapshot hash != the section's persisted one — grade the drafted world.

    Carries the section id and both hashes so the caller/audit can see the drift. Raised rather
    than grading, because a drifted snapshot means constraints/plan changed since the section was
    drafted — the judge must grade the EXACT prompt the drafter saw (inv 13), so this fails loud.
    """

    def __init__(self, *, section_id: str, drafted_hash: str, rebuilt_hash: str) -> None:
        self.section_id = section_id
        self.drafted_hash = drafted_hash
        self.rebuilt_hash = rebuilt_hash
        super().__init__(
            f"snapshot drift for section {section_id!r}: drafted {drafted_hash} != "
            f"rebuilt {rebuilt_hash} (constraints/plan changed since drafting)"
        )


class JudgeUnavailable(Exception):
    """The semantic pass could not run (provider not configured or budget exhausted).

    A typed refusal the engine decides on: the deterministic findings still stand and G3 shows the
    semantic pass as unavailable — the pass is honest rather than silently clean.
    """

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"semantic judge unavailable: {reason}")


def _planned_by_id(plan: StrategyPlan) -> dict[str, PlannedSection]:
    """The plan's sections indexed by ``section_id`` (validated :class:`PlannedSection`s)."""
    return {ps.section_id: ps for ps in (PlannedSection.model_validate(s) for s in plan.sections)}


def _persisted_hash(section: DraftSection) -> str:
    """The ``input_hash`` persisted on a section's ``prompt_snapshot`` (``""`` when absent)."""
    snapshot = section.prompt_snapshot if isinstance(section.prompt_snapshot, dict) else {}
    value = snapshot.get("input_hash")
    return value if isinstance(value, str) else ""


def _assert_snapshot_symmetry(
    db: Session,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    section: DraftSection,
    planned: PlannedSection,
    constraints: HardConstraintInputs,
) -> None:
    """Rebuild the section's snapshot and raise :class:`SnapshotDrift` on a hash mismatch."""
    rebuilt = build_snapshot(
        db, matter=matter, plan=plan, draft=draft, planned=planned, constraints=constraints
    )
    drafted = _persisted_hash(section)
    if rebuilt.input_hash != drafted:
        raise SnapshotDrift(
            section_id=section.section_id,
            drafted_hash=drafted,
            rebuilt_hash=rebuilt.input_hash,
        )


def _build_judge_prompt(section: DraftSection, *, insist_json: bool) -> str:
    """Assemble the judge prompt from the section's PERSISTED snapshot blocks + rendered preview.

    The persisted ``prompt_snapshot`` (rules_blocks + matter_directives + final_hard_constraints)
    is handed over VERBATIM — the symmetry: the judge grades against the same prompt the drafter
    saw. It flags ONLY the three semantic kinds with a one-sentence detail each, and returns
    ``{"findings": []}`` when the section is clean. ``insist_json`` appends the stricter retry
    suffix.
    """
    snapshot = section.prompt_snapshot if isinstance(section.prompt_snapshot, dict) else {}
    rules_blocks = snapshot.get("rules_blocks") or []
    matter_directives = snapshot.get("matter_directives") or []
    final_hard_constraints = snapshot.get("final_hard_constraints") or []

    blocks = "\n\n".join(str(b) for b in rules_blocks)
    directives = "\n".join(str(d) for d in matter_directives)
    constraints = "\n".join(f"- {c}" for c in final_hard_constraints)
    rendered = section.rendered_preview or ""

    prompt = (
        "You are a compliance reviewer for a personal-injury demand letter. Grade the SECTION "
        "below against the exact instructions the drafter was given. Flag ONLY these semantic "
        "problems — nothing mechanical (a code checker owns tokens, amounts, anchors, and "
        f"exhibits):\n"
        f"- {CheckKind.UNSUPPORTED_CAUSATION.value}: a causal claim the record does not support.\n"
        f"- {CheckKind.STRATEGY_DRIFT.value}: the section departs from the attorney's strategy / "
        "emphasis directives.\n"
        f"- {CheckKind.TONE.value}: tone unfit for a demand letter (e.g. inflammatory, hedged, "
        "unprofessional).\n\n"
        "The drafter's instructions (verbatim):\n"
        "--- RULES ---\n"
        f"{blocks}\n"
        "--- ATTORNEY DIRECTIVES ---\n"
        f"{directives}\n"
        "--- FINAL HARD CONSTRAINTS ---\n"
        f"{constraints}\n\n"
        "The rendered section to grade:\n"
        "---\n"
        f"{rendered}\n"
        "---\n\n"
        f"Return exactly one JSON object and nothing else: "
        '{"findings": [{"check_kind": "<one of '
        f"{list(_SEMANTIC_KINDS)}"
        '>", "section_id": '
        f'"{section.section_id}", "detail": "<one sentence>"}}, ...]}}. '
        'Return {"findings": []} when the section is clean.'
    )
    if insist_json:
        prompt += "\n\nReturn ONLY the JSON object — no prose, no code fences."
    return prompt


def _parse_batch(text: str) -> JudgeFindingBatch:
    """Extract the JSON object from a judge reply and validate it (house pattern).

    Tolerates surrounding prose / code fences by taking the substring from the first ``{`` to the
    last ``}`` before ``json.loads``. Raises on malformed/absent JSON — OR when a finding claims a
    mechanical ``check_kind`` (the :class:`JudgeFindingBatch` schema rejects it, inv 13) — and the
    caller turns that into the single metered retry.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in judge reply")
    payload = json.loads(text[start : end + 1])
    return JudgeFindingBatch.model_validate(payload)


def _judge_one_section(
    db: Session,
    client: MeteredLLMClient,
    *,
    matter: Matter,
    draft: DemandDraft,
    section: DraftSection,
) -> list[ComplianceFinding]:
    """Run the judge for ONE section, retrying ONCE on a parse/validation miss.

    A double failure emits ONE blocking ``tone`` finding (the fail-visible manual-review marker;
    see the module doc for why ``tone`` is reused rather than a new kind). Provider/budget errors
    propagate to :func:`run_judge`'s :class:`JudgeUnavailable` handling.
    """
    model = get_settings().judge_model
    first = client.complete(
        stage=_JUDGE_STAGE,
        model=model,
        prompt=_build_judge_prompt(section, insist_json=False),
    )
    try:
        batch = _parse_batch(first.text)
    except (ValueError, json.JSONDecodeError):
        _LOG.warning(
            "judge reply parse failed for section %s; one stricter retry", section.section_id
        )
        retry = client.complete(
            stage=_JUDGE_STAGE,
            model=model,
            prompt=_build_judge_prompt(section, insist_json=True),
        )
        try:
            batch = _parse_batch(retry.text)
        except (ValueError, json.JSONDecodeError) as exc:
            _LOG.error(
                "judge returned no valid verdict for section %s after retry (%s); "
                "emitting a manual-review finding",
                section.section_id,
                exc,
            )
            # Fail-visible, NOT fail-silent: a section the judge could not grade is surfaced as a
            # blocking finding the attorney dispositions, never passed as clean. Reuse the TONE
            # kind (the generic semantic kind) so the marker rides the semantic bucket without a
            # new check_kind — the schema-gate stays intact.
            return [
                ComplianceFinding(
                    draft_id=draft.id,
                    section_id=section.section_id,
                    registry_version=draft.registry_version,
                    check_kind=CheckKind.TONE.value,
                    detail=(
                        "semantic judge failed to return a valid verdict for this section — "
                        "manual review required"
                    ),
                    anchors=[],
                    span=None,
                )
            ]

    findings: list[ComplianceFinding] = []
    for jf in batch.findings:
        findings.append(
            ComplianceFinding(
                draft_id=draft.id,
                section_id=jf.section_id or section.section_id,
                registry_version=draft.registry_version,
                check_kind=jf.check_kind.value,
                detail=jf.detail,
                anchors=[],
                span=None,
            )
        )
    return findings


def run_judge(
    db: Session,
    client: MeteredLLMClient,
    *,
    matter: Matter,
    plan: StrategyPlan,
    draft: DemandDraft,
    sections: Sequence[DraftSection],
) -> list[ComplianceFinding]:
    """Grade every section for the semantic kinds; return OPEN semantic findings (uncommitted).

    For each section (in the order given): assert snapshot symmetry (rebuild the drafter snapshot
    from freshly-built constraints + the planned contract and compare ``input_hash`` — a mismatch
    raises :class:`SnapshotDrift`), then run ONE judge call. Findings are created OPEN with the
    semantic ``check_kind`` / detail (severity/bucket applied by the engine). A section with no
    matching planned contract is skipped for symmetry (it cannot be re-derived) — but that is an
    upstream inconsistency, so it is logged.

    Raises :class:`JudgeUnavailable` if the provider is not configured or the budget is exhausted
    (the engine decides — the deterministic findings still stand). Snapshot symmetry is checked
    for ALL sections BEFORE any judge call, so a drift fails the pass before spending on a model.
    """
    constraints = build_hard_constraints(db, matter=matter)
    planned_by_id = _planned_by_id(plan)

    # Symmetry gate for every section first — a drift must fail the pass before any spend.
    for section in sections:
        planned = planned_by_id.get(section.section_id)
        if planned is None:
            _LOG.warning(
                "section %s has no matching planned contract; skipping symmetry check",
                section.section_id,
            )
            continue
        _assert_snapshot_symmetry(
            db,
            matter=matter,
            plan=plan,
            draft=draft,
            section=section,
            planned=planned,
            constraints=constraints,
        )

    findings: list[ComplianceFinding] = []
    try:
        for section in sections:
            findings.extend(
                _judge_one_section(db, client, matter=matter, draft=draft, section=section)
            )
    except ProviderNotConfigured as exc:
        raise JudgeUnavailable(reason="provider_not_configured") from exc
    except BudgetExceededError as exc:
        raise JudgeUnavailable(reason="budget_exceeded") from exc
    return findings


__all__ = [
    "JudgeUnavailable",
    "SnapshotDrift",
    "run_judge",
]
