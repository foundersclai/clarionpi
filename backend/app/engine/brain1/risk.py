"""The risk-flag engine — anchored adverse-fact detection + the G2a disposition workflow.

This module is Brain-1's *surface-the-risk* stage (risk_flag_engine / system_contract §2, 6, 8,
13). It detects adverse / case-risk facts, emits anchored :class:`~app.models.orm.RiskFlag` rows,
and drives the G2a disposition an attorney (or, for low/medium, a paralegal) records over them —
the output of which becomes hard constraints for Brain-2 and checks for the compliance panel.

Four structural rules carry this module's invariants:

* **Surface always, suppress never (inv 6).** This engine's one forbidden move is dropping an
  adverse fact. The per-kind cap in :class:`~app.core.config.Settings` bounds *display* volume for
  the UI; it is **not** enforced here as suppression — every derived flag is persisted. The
  no-volunteer discipline (a flag never reaches the letter unless dispositioned
  ``address_in_letter``) lives downstream in Brain-2 / compliance; here we only ensure the flags
  exist to be dispositioned.

* **Detector provenance is honest (inv 13).** Deterministic detectors are pure code
  (``FlagDetector.DATE_MATH`` — date/amount arithmetic, no regex on clinical language); the
  semantic labeling pass is an LLM call through the single metered door
  (``FlagDetector.HEURISTIC_LLM``). No code-side regex ever reads clinical prose to decide a
  semantic kind.

* **Every LLM label is page-anchored or rejected (inv 2).** A label's ``anchor_pages`` are
  validated against the matter's known page set; a label citing a page outside that set is
  rejected whole (counted, logged) — a fabricated cite never persists.

* **High-severity disposition is attorney-only, server-enforced (inv 8).** :func:`disposition_flag`
  refuses a non-attorney disposition of a HIGH flag with a typed error the route maps to 403;
  paralegals may disposition low/medium.

Idempotent re-run discipline (flow_04): a re-run PRESERVES flags that already carry a disposition
(attorney work is never recreated or deleted) and re-derives only the open (undispositioned)
machine flags. A freshly-derived candidate that matches a preserved dispositioned flag is skipped
so no duplicate appears.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.core.tenancy import tenant_add
from app.models.enums import (
    FlagDetector,
    FlagKind,
    FlagSeverity,
    UserRole,
)
from app.models.orm import (
    IncidentFacts,
    Matter,
    MedicalEncounter,
    RiskFlag,
    StrategyInputs,
    User,
)
from app.models.schemas import FlagDispositionRequest, RiskLabelBatch

_LOG = logging.getLogger("clarionpi.risk")

# The LLM risk-labeling stage id on the metering ledger.
_LABEL_STAGE = "analysis.risk_flags"

# The audit event kinds written by this module.
_RUN_AUDIT_KIND = "risk_flags_generated"
_DISPOSITION_AUDIT_KIND = "risk_flag_dispositioned"

# The six SEMANTIC flag kinds the LLM labeling pass owns. The deterministic detectors own
# TREATMENT_GAP and LOW_PROPERTY_DAMAGE; THIRD_PARTY_PHI is in scope for the labeling pass (a
# third party's PHI on a page is a semantic read of the record, not a rule).
_LLM_FLAG_KINDS: frozenset[FlagKind] = frozenset(
    {
        FlagKind.PREEXISTING_CONDITION,
        FlagKind.PRIOR_CLAIM,
        FlagKind.DEGENERATIVE_FINDING,
        FlagKind.CAUSATION_AMBIGUITY,
        FlagKind.LIABILITY_WEAKNESS,
        FlagKind.THIRD_PARTY_PHI,
    }
)

# Design-pinned severity taxonomy for the LLM kinds (risk_flag_engine §4 / 01 §7). The model's
# claimed severity is TRUSTED but CLAMPED to this table if it disagrees — the taxonomy is a
# design decision, not model judgment, and the clamp is deterministic policy (not a semantic
# rewrite of the label's text).
_KIND_SEVERITY: dict[FlagKind, FlagSeverity] = {
    FlagKind.PREEXISTING_CONDITION: FlagSeverity.HIGH,
    FlagKind.PRIOR_CLAIM: FlagSeverity.HIGH,
    FlagKind.CAUSATION_AMBIGUITY: FlagSeverity.HIGH,
    FlagKind.LIABILITY_WEAKNESS: FlagSeverity.HIGH,
    FlagKind.THIRD_PARTY_PHI: FlagSeverity.HIGH,
    FlagKind.DEGENERATIVE_FINDING: FlagSeverity.MEDIUM,
}


# --------------------------------------------------------------------------------------
# Run outcome
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskRunOutcome:
    """The accounting of one :func:`run_risk_detectors` pass.

    ``deterministic_flags`` / ``llm_flags`` count what this run PRODUCED (freshly created rows);
    ``anchors_rejected`` counts LLM labels dropped for citing a page outside the matter's known
    page set; ``llm_skipped`` is set when the labeling pass did not run (no client, provider
    unavailable, or budget) — deterministic flags are still produced. ``preserved_dispositioned``
    counts dispositioned flags kept across the re-run (never recreated); ``replaced_open`` counts
    prior OPEN machine flags this run superseded.
    """

    deterministic_flags: int
    llm_flags: int
    anchors_rejected: int
    llm_skipped: bool
    preserved_dispositioned: int
    replaced_open: int


# --------------------------------------------------------------------------------------
# Anchor helpers
# --------------------------------------------------------------------------------------


def _anchor_dicts(anchors: object) -> list[dict]:
    """Normalize an ``anchors`` JSON column to a list of plain dicts (defensive copy)."""
    if not isinstance(anchors, list):
        return []
    return [dict(a) for a in anchors if isinstance(a, dict)]


def _anchor_key(anchors: list[dict]) -> tuple[tuple[str, int], ...]:
    """A sorted, hashable ``(document_id, page)`` key for a flag's anchor set.

    This is the dedup identity of a flag alongside its ``kind``: two candidates with the same kind
    and the same set of ``(document_id, page)`` anchors are the same finding. ``document_id`` is
    stringified so a UUID vs str mismatch across JSON round-trips does not split the key.
    """
    pairs = {
        (str(a["document_id"]), int(a["page"]))
        for a in anchors
        if a.get("document_id") is not None and a.get("page") is not None
    }
    return tuple(sorted(pairs))


def _valid_page_set(
    encounters: list[MedicalEncounter], incident: IncidentFacts | None
) -> set[tuple[str, int]]:
    """The matter-wide set of valid ``(document_id, page)`` anchors.

    Union of every encounter's anchors and the incident-facts anchors. An LLM label may only cite
    pages that exist here; anything else is a fabricated cite (inv 2).
    """
    valid: set[tuple[str, int]] = set()
    for enc in encounters:
        for a in _anchor_dicts(enc.anchors):
            if a.get("document_id") is not None and a.get("page") is not None:
                valid.add((str(a["document_id"]), int(a["page"])))
    if incident is not None:
        for a in _anchor_dicts(incident.anchors):
            if a.get("document_id") is not None and a.get("page") is not None:
                valid.add((str(a["document_id"]), int(a["page"])))
    return valid


# --------------------------------------------------------------------------------------
# Deterministic detectors (FlagDetector.DATE_MATH)
# --------------------------------------------------------------------------------------
#
# DATE_MATH is the deterministic-arithmetic bucket: it covers the treatment-gap DATE math AND the
# low-property-damage AMOUNT comparison. Both are pure code over authoritative fields (encounter
# dates, the attorney-set G1.5 MMI date + property-damage estimate) — no LLM, no regex on prose.


@dataclass(frozen=True)
class _Candidate:
    """A derived flag candidate, pre-persistence — the common shape both detector families emit."""

    kind: FlagKind
    severity: FlagSeverity
    detector: FlagDetector
    anchors: list[dict]
    detail: str


def _treatment_gap_candidates(
    encounters: list[MedicalEncounter], strategy: StrategyInputs | None
) -> list[_Candidate]:
    """One HIGH ``treatment_gap`` candidate per consecutive-encounter gap wider than the threshold.

    Encounters are sorted by ``date_of_service``; each consecutive pair whose day delta is
    STRICTLY GREATER than ``settings.treatment_gap_max_days`` is a gap (exactly-threshold is fine —
    the boundary is ``>`` not ``>=``). When an MMI date is set, only gaps whose LATER encounter is
    on/before MMI count — a gap after maximum medical improvement is expected, not adverse. With MMI
    unset, ALL gaps count and the detail notes MMI is unset (a G1.5 input the attorney has not yet
    supplied). Anchors are the union of the two bounding encounters' anchors. The detail carries
    only ISO dates (fact-shaped; provider names are fine in attorney UI but add no signal here).
    """
    if len(encounters) < 2:
        return []
    max_gap = get_settings().treatment_gap_max_days
    mmi: date | None = strategy.mmi_date if strategy is not None else None

    ordered = sorted(encounters, key=lambda e: e.date_of_service)
    candidates: list[_Candidate] = []
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        gap_days = (later.date_of_service - earlier.date_of_service).days
        if gap_days <= max_gap:
            continue
        # Post-MMI gaps are expected — only count a gap whose LATER encounter is on/before MMI.
        if mmi is not None and later.date_of_service > mmi:
            continue
        detail = (
            f"No treatment recorded for {gap_days} days between "
            f"{earlier.date_of_service.isoformat()} and {later.date_of_service.isoformat()}"
        )
        if mmi is None:
            detail += " (MMI not set (G1.5))"
        anchors = _anchor_dicts(earlier.anchors) + _anchor_dicts(later.anchors)
        candidates.append(
            _Candidate(
                kind=FlagKind.TREATMENT_GAP,
                severity=FlagSeverity.HIGH,
                detector=FlagDetector.DATE_MATH,
                anchors=anchors,
                detail=detail,
            )
        )
    return candidates


def _low_property_damage_candidates(
    encounters: list[MedicalEncounter], strategy: StrategyInputs | None
) -> list[_Candidate]:
    """At most one MEDIUM ``low_property_damage`` candidate.

    Fires iff the attorney set ``property_damage_estimate_cents`` (G1.5), it is BELOW
    ``settings.low_property_damage_threshold_cents``, AND the matter has ≥1 encounter (injury
    treatment paired with de-minimis property damage is the adverse signal). Anchors are ``[]`` —
    this is intake-derived, so no page exists to cite (the one anchors-optional case in this
    engine; the detail cites the G1.5 field as its source).
    """
    if strategy is None or strategy.property_damage_estimate_cents is None:
        return []
    if not encounters:
        return []
    threshold = get_settings().low_property_damage_threshold_cents
    estimate = strategy.property_damage_estimate_cents
    if estimate >= threshold:
        return []
    detail = (
        f"Property-damage estimate ({estimate} cents, attorney intake G1.5) is below the "
        f"low-damage threshold ({threshold} cents) with injury treatment on file"
    )
    return [
        _Candidate(
            kind=FlagKind.LOW_PROPERTY_DAMAGE,
            severity=FlagSeverity.MEDIUM,
            # DATE_MATH is the deterministic-arithmetic bucket — it covers this amount comparison
            # as well as the treatment-gap date math (no LLM, no regex).
            detector=FlagDetector.DATE_MATH,
            anchors=[],
            detail=detail,
        )
    ]


# --------------------------------------------------------------------------------------
# LLM labeling pass (FlagDetector.HEURISTIC_LLM)
# --------------------------------------------------------------------------------------


def _encounter_page_set(encounter: MedicalEncounter) -> list[int]:
    """The sorted, de-duplicated valid page numbers for one encounter (from its anchors)."""
    pages = {int(a["page"]) for a in _anchor_dicts(encounter.anchors) if a.get("page") is not None}
    return sorted(pages)


def _build_label_prompt(
    encounters: list[MedicalEncounter], incident: IncidentFacts | None, *, insist_json: bool
) -> str:
    """Assemble the ONE risk-labeling digest prompt.

    Per encounter the digest lists a short key (``E1, E2...``), DOS, encounter_type, the clinical
    lists, and the encounter's VALID page set. The incident payload (police-report facts) is
    appended with its valid pages. The model is instructed to emit flags of EXACTLY the six
    semantic kinds, each citing ``anchor_pages`` drawn from the listed valid pages, with severity
    per the design taxonomy. ``insist_json`` appends the stricter retry suffix.
    """
    kind_lines = "\n".join(
        f"- {k.value} (severity: {_KIND_SEVERITY[k].value})"
        for k in sorted(_LLM_FLAG_KINDS, key=lambda k: k.value)
    )
    enc_blocks: list[str] = []
    for i, enc in enumerate(encounters, start=1):
        pages = _encounter_page_set(enc)
        enc_blocks.append(
            f"E{i} — DOS {enc.date_of_service.isoformat()}, type {enc.encounter_type}\n"
            f"  Complaints: {', '.join(str(c) for c in enc.complaints) or '(none)'}\n"
            f"  Findings: {', '.join(str(f) for f in enc.findings) or '(none)'}\n"
            f"  Diagnoses: {', '.join(str(d) for d in enc.diagnoses) or '(none)'}\n"
            f"  Valid pages: {pages or '(none)'}"
        )
    encounters_text = "\n\n".join(enc_blocks) if enc_blocks else "(no encounters)"

    if incident is not None:
        incident_pages = sorted(
            {int(a["page"]) for a in _anchor_dicts(incident.anchors) if a.get("page") is not None}
        )
        incident_text = (
            f"Incident facts (police report / intake): {json.dumps(incident.payload)}\n"
            f"  Valid pages: {incident_pages or '(none)'}"
        )
    else:
        incident_text = "Incident facts: (none)"

    prompt = (
        "You are reviewing a personal-injury case file to identify CASE-RISK flags an attorney "
        "must weigh before drafting a demand. Identify risk flags of EXACTLY these kinds — use one "
        "of these values for each flag's `kind`, and no others:\n"
        f"{kind_lines}\n\n"
        "Each flag MUST cite `anchor_pages` — 1-based page numbers taken from the 'Valid pages' "
        "listed for the encounter(s) or document(s) the flag derives from. Do not cite a page that "
        "is not listed as valid. Set `severity` to the value shown for that kind above.\n\n"
        "Encounters:\n"
        f"{encounters_text}\n\n"
        f"{incident_text}\n\n"
        "Return exactly one JSON object and nothing else: "
        '{"flags": [{"kind": "<one of the kinds>", "severity": "<low|medium|high>", '
        '"detail": "<short factual description>", "anchor_pages": [<int>, ...]}]}. '
        "Return an empty flags list if there are no risk flags."
    )
    if insist_json:
        prompt += "\n\nReturn ONLY the JSON object — no prose, no code fences."
    return prompt


def _parse_label_batch(text: str) -> RiskLabelBatch:
    """Extract the JSON object from a model reply and validate it into a :class:`RiskLabelBatch`.

    Tolerates surrounding prose / code fences by taking the substring from the first ``{`` to the
    last ``}`` before ``json.loads`` (house pattern). Raises on malformed/absent JSON or a value
    outside the schema (e.g. an unknown ``kind``) — the caller turns that into the single metered
    retry.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in risk-label reply")
    payload = json.loads(text[start : end + 1])
    return RiskLabelBatch.model_validate(payload)


def _run_labeler(
    client: MeteredLLMClient,
    encounters: list[MedicalEncounter],
    incident: IncidentFacts | None,
) -> RiskLabelBatch:
    """Call the metered client for the labeling pass, retrying ONCE with a stricter prompt.

    Both attempts go through the meter (a wasted attempt is still a real call). Provider/budget
    errors are NOT caught here — they belong to the caller's expected-offline path. A second parse
    failure re-raises the parse error (the caller treats that as a skip).
    """
    model = get_settings().risk_label_model
    first = client.complete(
        stage=_LABEL_STAGE,
        model=model,
        prompt=_build_label_prompt(encounters, incident, insist_json=False),
    )
    try:
        return _parse_label_batch(first.text)
    except (ValueError, json.JSONDecodeError):
        pass  # fall through to the single stricter retry
    retry = client.complete(
        stage=_LABEL_STAGE,
        model=model,
        prompt=_build_label_prompt(encounters, incident, insist_json=True),
    )
    return _parse_label_batch(retry.text)


def _label_to_candidate(label: object, valid_pages: set[tuple[str, int]]) -> _Candidate | None:
    """Turn ONE validated :class:`RiskLabelOutput` into a candidate, or ``None`` if rejected.

    Anchor validation (inv 2): the label's ``anchor_pages`` are plain page ints. The label is
    accepted iff EVERY cited page number matches SOME valid ``(document_id, page)`` — the stored
    anchors become every valid pair whose page is cited (deterministic doc order). A cited page not
    present in the matter-wide valid set rejects the WHOLE label (the caller counts
    ``anchors_rejected``). Per-encounter anchor precision (which document a page belongs to when two
    documents share a page number) is bounded by M4 and improves at S1/bbox time — here a cited page
    is anchored to every document that has it.

    Kind that is not one of the six semantic kinds is ignored (defensive — the schema already
    constrains ``kind`` to the FlagKind enum, but the deterministic kinds must not arrive via the
    LLM path). Severity is CLAMPED to the design taxonomy: the model's claim is trusted but a
    disagreement is overwritten deterministically (policy, not a semantic rewrite).
    """
    kind: FlagKind = label.kind  # type: ignore[attr-defined]
    if kind not in _LLM_FLAG_KINDS:
        _LOG.warning("risk label emitted non-semantic kind %s; ignoring", kind.value)
        return None

    cited_pages = {int(p) for p in label.anchor_pages}  # type: ignore[attr-defined]
    # A page number maps to every valid (document_id, page) sharing that page (deterministic doc
    # order). Reject the whole label if ANY cited page is outside the matter-wide valid set.
    valid_page_numbers = {page for (_doc, page) in valid_pages}
    if not cited_pages.issubset(valid_page_numbers):
        return None

    anchors: list[dict] = []
    for doc_id, page in sorted(valid_pages):
        if page in cited_pages:
            anchors.append({"document_id": doc_id, "page": page})
    if not anchors:
        # No concrete anchor pair though the page numbers matched — treat as a rejected label
        # rather than persist an unanchored LLM flag (inv 2).
        return None

    # Clamp severity to the design taxonomy (deterministic policy).
    severity = _KIND_SEVERITY[kind]
    return _Candidate(
        kind=kind,
        severity=severity,
        detector=FlagDetector.HEURISTIC_LLM,
        anchors=anchors,
        detail=str(label.detail),  # type: ignore[attr-defined]
    )


def _llm_candidates(
    client: MeteredLLMClient,
    encounters: list[MedicalEncounter],
    incident: IncidentFacts | None,
    valid_pages: set[tuple[str, int]],
) -> tuple[list[_Candidate], int, bool]:
    """Run the labeling pass and turn it into candidates.

    Returns ``(candidates, anchors_rejected, skipped)``. ``skipped`` is True when the pass did not
    run to a parseable result: a ``ProviderNotConfigured`` / ``BudgetExceededError`` (expected
    offline) OR a second parse failure — in every case the deterministic flags still stand. Each
    out-of-set label is dropped and counted in ``anchors_rejected``.
    """
    try:
        batch = _run_labeler(client, encounters, incident)
    except (ProviderNotConfigured, BudgetExceededError) as exc:
        _LOG.warning("risk labeling unavailable (%s); skipping the LLM pass", type(exc).__name__)
        return [], 0, True
    except (ValueError, json.JSONDecodeError) as exc:
        _LOG.warning("risk labeling failed to parse twice (%s); skipping the LLM pass", exc)
        return [], 0, True

    candidates: list[_Candidate] = []
    anchors_rejected = 0
    for label in batch.flags:
        candidate = _label_to_candidate(label, valid_pages)
        if candidate is None:
            anchors_rejected += 1
            _LOG.warning(
                "risk label rejected (out-of-set anchor or non-semantic kind): kind=%s pages=%s",
                getattr(label, "kind", None),
                getattr(label, "anchor_pages", None),
            )
            continue
        candidates.append(candidate)
    return candidates, anchors_rejected, False


# --------------------------------------------------------------------------------------
# Run — idempotent re-derivation + persist
# --------------------------------------------------------------------------------------


def _existing_flags(db: Session, *, matter: Matter) -> list[RiskFlag]:
    """The matter's current risk flags."""
    return list(db.execute(select(RiskFlag).where(RiskFlag.matter_id == matter.id)).scalars())


def run_risk_detectors(
    db: Session, client: MeteredLLMClient | None, *, matter: Matter
) -> RiskRunOutcome:
    """Detect risk flags for ``matter`` and persist them, idempotently.

    Steps (see the module docstring for the invariants each upholds):

    1. **Idempotent re-run (flow_04).** Load existing flags. Flags WITH a disposition are
       PRESERVED — never recreated or deleted (attorney/paralegal work persists across re-runs).
       Flags with ``disposition IS NULL`` (open, machine-produced) are DELETED and re-derived fresh
       (counted ``replaced_open``). A freshly-derived candidate matching a preserved dispositioned
       flag on ``(kind, sorted (document_id, page) anchor set)`` is SKIPPED (counted
       ``preserved_dispositioned``) so no duplicate appears.
    2. **Deterministic detectors** (``DATE_MATH``): ``treatment_gap`` (date math, MMI-aware) and
       ``low_property_damage`` (amount comparison, intake-derived, anchors ``[]``).
    3. **LLM labeling pass** (``HEURISTIC_LLM``): ``client is None`` -> skipped; otherwise one
       digest prompt over the encounters + incident, parsed with the house one-retry, each label
       page-set-validated (out-of-set -> rejected + counted), severity clamped to the taxonomy.
    4. All new flags ``tenant_add``'d; a single commit at the end; a ``risk_flags_generated`` audit
       event with the counts.

    Never suppresses (inv 6): the per-kind cap in settings is a UI display bound, not applied here.
    """
    encounters = _load_encounters(db, matter=matter)
    strategy = _load_strategy(db, matter=matter)
    incident = _load_incident(db, matter=matter)

    existing = _existing_flags(db, matter=matter)
    preserved = [f for f in existing if f.disposition is not None]
    open_flags = [f for f in existing if f.disposition is None]

    # Re-derive from scratch: delete the OPEN machine flags (they are recomputed below); the
    # dispositioned ones are preserved untouched.
    replaced_open = len(open_flags)
    for flag in open_flags:
        db.delete(flag)

    # The dedup identity of every preserved flag: (kind, sorted anchor set). A fresh candidate that
    # matches one of these is skipped, so an already-dispositioned fact is never duplicated.
    preserved_keys: set[tuple[str, tuple[tuple[str, int], ...]]] = {
        (flag.kind, _anchor_key(_anchor_dicts(flag.anchors))) for flag in preserved
    }

    # --- Deterministic detectors (DATE_MATH: date math + amount math) ---
    deterministic = _treatment_gap_candidates(encounters, strategy)
    deterministic += _low_property_damage_candidates(encounters, strategy)

    # --- LLM labeling pass (HEURISTIC_LLM) ---
    llm: list[_Candidate] = []
    anchors_rejected = 0
    llm_skipped = True
    if client is not None:
        valid_pages = _valid_page_set(encounters, incident)
        llm, anchors_rejected, llm_skipped = _llm_candidates(
            client, encounters, incident, valid_pages
        )

    # --- Persist, skipping any candidate that matches a preserved dispositioned flag ---
    deterministic_flags = 0
    llm_flags = 0
    preserved_dispositioned = 0
    for candidate in [*deterministic, *llm]:
        key = (candidate.kind.value, _anchor_key(candidate.anchors))
        if key in preserved_keys:
            preserved_dispositioned += 1
            continue
        flag = RiskFlag(
            matter_id=matter.id,
            kind=candidate.kind.value,
            severity=candidate.severity.value,
            detector=candidate.detector.value,
            anchors=candidate.anchors,
            detail=candidate.detail,
        )
        tenant_add(db, flag, matter.firm_id)
        if candidate.detector is FlagDetector.HEURISTIC_LLM:
            llm_flags += 1
        else:
            deterministic_flags += 1

    outcome = RiskRunOutcome(
        deterministic_flags=deterministic_flags,
        llm_flags=llm_flags,
        anchors_rejected=anchors_rejected,
        llm_skipped=llm_skipped,
        preserved_dispositioned=preserved_dispositioned,
        replaced_open=replaced_open,
    )
    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=None,
        event_kind=_RUN_AUDIT_KIND,
        payload={
            "matter_id": str(matter.id),
            "deterministic_flags": deterministic_flags,
            "llm_flags": llm_flags,
            "anchors_rejected": anchors_rejected,
            "llm_skipped": llm_skipped,
            "preserved_dispositioned": preserved_dispositioned,
            "replaced_open": replaced_open,
        },
    )
    db.commit()
    return outcome


def _load_encounters(db: Session, *, matter: Matter) -> list[MedicalEncounter]:
    """The matter's encounters (unordered — detectors sort as they need)."""
    return list(
        db.execute(
            select(MedicalEncounter).where(MedicalEncounter.matter_id == matter.id)
        ).scalars()
    )


def _load_strategy(db: Session, *, matter: Matter) -> StrategyInputs | None:
    """The matter's one StrategyInputs row (MMI + property-damage estimate), or ``None``."""
    return db.execute(
        select(StrategyInputs).where(StrategyInputs.matter_id == matter.id)
    ).scalar_one_or_none()


def _load_incident(db: Session, *, matter: Matter) -> IncidentFacts | None:
    """The matter's one IncidentFacts row, or ``None``."""
    return db.execute(
        select(IncidentFacts).where(IncidentFacts.matter_id == matter.id)
    ).scalar_one_or_none()


# --------------------------------------------------------------------------------------
# Disposition workflow (G2a)
# --------------------------------------------------------------------------------------


class HighSeverityDispositionForbidden(Exception):
    """A non-attorney tried to disposition a HIGH-severity flag (inv 8).

    Carries the required and actual roles so the route maps it to a typed 403. Paralegals MAY
    disposition low/medium flags — this refusal is only for HIGH.
    """

    def __init__(self, *, actual: str) -> None:
        self.required_role = UserRole.ATTORNEY.value
        self.actual = actual
        super().__init__(
            f"high-severity risk-flag disposition requires role "
            f"{UserRole.ATTORNEY.value!r}; actor role is {actual!r}"
        )


def disposition_flag(
    db: Session, *, user: User, flag: RiskFlag, request: FlagDispositionRequest
) -> RiskFlag:
    """Record a human disposition on ``flag`` (the G2a per-flag act).

    * **Role gate (inv 8, server-enforced):** a HIGH-severity flag requires an attorney; a
      non-attorney actor raises :class:`HighSeverityDispositionForbidden` (route -> 403). Low/medium
      flags are prep-capable — a paralegal may disposition them.
    * Sets ``disposition``, ``disposition_by``, ``disposition_role`` (the actor's role at
      disposition time — an audit denormalization), and ``disposition_rationale``
      (``omit_with_rationale`` requires a non-blank rationale, enforced at the schema).
    * Writes a ``risk_flag_dispositioned`` audit event and commits.

    **Re-disposition is allowed** and OVERWRITES the prior disposition with a fresh audit event.
    G2a is not confirmed yet, so an attorney may change their mind pre-freeze; post-confirm changes
    come through flow_04 rework, not this path.
    """
    if flag.severity == FlagSeverity.HIGH.value and user.role != UserRole.ATTORNEY.value:
        raise HighSeverityDispositionForbidden(actual=user.role)

    flag.disposition = request.disposition.value
    flag.disposition_by = user.id
    flag.disposition_role = user.role
    flag.disposition_rationale = request.rationale
    db.add(flag)

    record_event(
        db,
        firm_id=flag.firm_id,
        actor_id=user.id,
        event_kind=_DISPOSITION_AUDIT_KIND,
        payload={
            "flag_id": str(flag.id),
            "kind": flag.kind,
            "severity": flag.severity,
            "disposition": request.disposition.value,
            "role": user.role,
        },
    )
    db.commit()
    return flag


# --------------------------------------------------------------------------------------
# Guard-context feed
# --------------------------------------------------------------------------------------


def open_high_severity_count(db: Session, *, matter: Matter) -> int:
    """Count the matter's OPEN high-severity flags — the G2a-confirm guard feed.

    "Open high-severity" is defined as ``severity == high AND disposition IS NULL`` — the SAME
    definition the orchestrator's ``build_guard_context`` counts inline
    (``app.engine.orchestrator.service``). This function is the shared, named home for that
    predicate; the two MUST agree (a HIGH flag left undispositioned blocks G2a confirm, or is
    proceeded over via an audited override — guards ``high_severity_dispositioned_or_override``).
    """
    return db.execute(
        select(func.count())
        .select_from(RiskFlag)
        .where(
            RiskFlag.matter_id == matter.id,
            RiskFlag.severity == FlagSeverity.HIGH.value,
            RiskFlag.disposition.is_(None),
        )
    ).scalar_one()
