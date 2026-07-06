"""Encounter merge — deterministic collapse first, LLM tiebreak for near-matches only (inv 13).

Two windows (or two cross-pulled record sets) can each emit the *same* clinical encounter. This
module collapses those duplicates:

* **Deterministic-first (inv 13).** Exact-key duplicates — same
  ``(provider, date_of_service, encounter_type)`` under casefold + whitespace collapse — merge by
  RULE into the earliest-created survivor, no model call. This is the mechanical majority.
* **LLM tiebreak (near-matches only).** Pairs that are *not* exact-key-equal but share a
  ``date_of_service`` and have provider token-Jaccard ≥ 0.5 are genuinely ambiguous ("Dr. J.
  Alvarez" vs "Alvarez, J."). ONLY these go to the ``merge_tiebreak`` model. If the model is
  absent/unavailable or the answer won't parse, the pair is LEFT UNMERGED and counted — code
  never guesses a clinical-identity call; unmerged near-dupes surface at G2a.

**Reversibility.** Before an absorbed row is deleted, a full JSON-safe snapshot of its business
fields is appended to the survivor's ``merged_from`` — the merge is reversible by construction.

Deterministic iteration order (``created_at, id``) throughout, so the survivor choice and the
result are stable; re-running on already-merged data finds no groups (idempotent).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.models.enums import MergeBasis
from app.models.orm import Matter, MedicalEncounter

# The near-match provider-name Jaccard floor for eligibility to the LLM tiebreak.
_TIEBREAK_JACCARD_FLOOR = 0.5
# The tiebreak stage id on the metering ledger.
_TIEBREAK_STAGE = "extract.merge"
_JSON_ONLY_SUFFIX = "\n\nReturn ONLY the JSON object — no prose, no code fences."

# The business fields snapshotted into merged_from (everything but bookkeeping columns).
_SNAPSHOT_FIELDS = (
    "date_of_service",
    "provider",
    "facility",
    "encounter_type",
    "complaints",
    "findings",
    "diagnoses",
    "procedures",
    "work_status",
    "narrative_tokenized",
    "anchors",
    "field_confidence",
    "merge_basis",
)
_LIST_FIELDS = ("complaints", "findings", "diagnoses", "procedures")


@dataclass(frozen=True)
class MergeOutcome:
    """Aggregate result of a merge pass over one matter's encounters.

    ``merged_groups`` counts survivor rows that absorbed ≥1 other; ``llm_tiebreaks`` counts pairs
    merged via a model adjudication; ``tiebreaks_skipped`` counts near-match pairs left unmerged
    because the model was absent/unavailable or its answer would not parse; ``encounters_remaining``
    is the surviving row count after the pass.
    """

    merged_groups: int
    llm_tiebreaks: int
    encounters_remaining: int
    tiebreaks_skipped: int


def _ws_collapse(value: str) -> str:
    """Casefold and collapse internal whitespace runs to single spaces, stripped."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _exact_key(row: MedicalEncounter) -> tuple[str, object, str]:
    """The deterministic merge key: (provider ws-collapsed casefolded, date, encounter_type)."""
    return (
        _ws_collapse(row.provider),
        row.date_of_service,
        _ws_collapse(row.encounter_type),
    )


def _provider_tokens(provider: str) -> set[str]:
    """Casefolded word-token set of a provider name, punctuation stripped (for Jaccard)."""
    return {tok for tok in re.split(r"[^a-z0-9]+", provider.casefold()) if tok}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two token sets; 0.0 when both are empty (no shared signal)."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _comparable_created_at(value: datetime | None) -> datetime | None:
    """Normalize ``created_at`` to a naive-UTC datetime so mixed awareness never breaks sorting.

    SQLite drops tzinfo on round-trip, so a freshly-loaded row is naive while a row whose
    ``created_at`` was just set in-session may be tz-aware; comparing the two raises. Coerce any
    aware value to its UTC instant, then strip tzinfo, giving one consistent, comparable form.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _order_key(row: MedicalEncounter) -> tuple:
    """Deterministic ordering key ``(created_at, id)`` — earliest is the survivor.

    ``created_at`` may be ``None`` on a not-yet-flushed row in tests; the leading flag sorts those
    last so a real timestamp still wins earliest-first, and ordering never raises.
    """
    created = _comparable_created_at(row.created_at)
    return (created is not None, created, str(row.id))


def _ordered_encounters(db: Session, *, matter: Matter) -> list[MedicalEncounter]:
    """All of the matter's encounters in deterministic ``(created_at, id)`` order."""
    rows = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).all()
    return sorted(rows, key=_order_key)


def _snapshot(row: MedicalEncounter) -> dict:
    """A JSON-safe snapshot of a row's business fields (for reversible ``merged_from``)."""
    snap: dict = {}
    for field in _SNAPSHOT_FIELDS:
        value = getattr(row, field)
        if field == "date_of_service" and value is not None:
            snap[field] = value.isoformat()
        else:
            snap[field] = value
    return snap


def _union_list(existing: list, incoming: list) -> list:
    """Order-preserving dedup union of two lists (the four clinical list fields)."""
    out = list(existing)
    seen = set(existing)
    for value in incoming:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _union_anchors(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Union anchor dicts, deduped by (document_id, page, window_id)."""
    out = list(existing)
    seen = {(d.get("document_id"), d.get("page"), d.get("window_id")) for d in existing}
    for anchor in incoming:
        key = (anchor.get("document_id"), anchor.get("page"), anchor.get("window_id"))
        if key not in seen:
            out.append(anchor)
            seen.add(key)
    return out


def _merge_field_confidence(existing: dict, incoming: dict) -> dict:
    """Per-field max of two confidence maps."""
    out = dict(existing)
    for key, value in incoming.items():
        if key not in out or value > out[key]:
            out[key] = value
    return out


def _absorb(
    db: Session, *, survivor: MedicalEncounter, absorbed: MedicalEncounter, basis: MergeBasis
) -> None:
    """Fold ``absorbed`` into ``survivor`` (reversibly), then delete the absorbed row.

    Survivor scalar fields are kept; the four clinical list fields union (order-preserving,
    deduped); anchors union; ``field_confidence`` takes the per-field max. Before deletion, a full
    snapshot of the absorbed row is appended to ``survivor.merged_from`` so the merge is reversible
    by construction.
    """
    # Reversibility: capture the absorbed row's full business state BEFORE mutating/deleting it.
    survivor.merged_from = list(survivor.merged_from) + [
        {"encounter_id": str(absorbed.id), "snapshot": _snapshot(absorbed)}
    ]
    for field in _LIST_FIELDS:
        merged = _union_list(getattr(survivor, field), getattr(absorbed, field))
        setattr(survivor, field, merged)
    survivor.anchors = _union_anchors(list(survivor.anchors), list(absorbed.anchors))
    survivor.field_confidence = _merge_field_confidence(
        dict(survivor.field_confidence), dict(absorbed.field_confidence)
    )
    survivor.merge_basis = basis.value
    db.delete(absorbed)


def _merge_exact_key_groups(
    db: Session, rows: list[MedicalEncounter]
) -> tuple[set[uuid.UUID], int]:
    """Collapse exact-key duplicate groups into their earliest survivor.

    Returns the set of absorbed (now-deleted) row ids and the count of survivor rows that absorbed
    at least one other. ``rows`` is already in ``(created_at, id)`` order, so the first row of each
    key group is its survivor.
    """
    survivors: dict[tuple, MedicalEncounter] = {}
    absorbed_ids: set[uuid.UUID] = set()
    merged_groups = 0
    grew: set[uuid.UUID] = set()
    for row in rows:
        key = _exact_key(row)
        survivor = survivors.get(key)
        if survivor is None:
            survivors[key] = row
            continue
        _absorb(db, survivor=survivor, absorbed=row, basis=MergeBasis.DETERMINISTIC_KEY)
        absorbed_ids.add(row.id)
        if survivor.id not in grew:
            grew.add(survivor.id)
            merged_groups += 1
    return absorbed_ids, merged_groups


def _tiebreak_prompt(a: MedicalEncounter, b: MedicalEncounter) -> str:
    """Prompt the tiebreak model with both rows' fields, asking the same-visit yes/no question."""

    def _fields(row: MedicalEncounter) -> dict:
        return {
            "date_of_service": row.date_of_service.isoformat(),
            "provider": row.provider,
            "facility": row.facility,
            "encounter_type": row.encounter_type,
            "complaints": list(row.complaints),
            "findings": list(row.findings),
            "diagnoses": list(row.diagnoses),
            "procedures": list(row.procedures),
        }

    return (
        "You are deciding whether two extracted medical-record entries describe the SAME single "
        "clinical visit (same encounter), or two different visits. They share a date of service "
        "but the provider names differ in form.\n\n"
        f"Entry A:\n{json.dumps(_fields(a), indent=2)}\n\n"
        f"Entry B:\n{json.dumps(_fields(b), indent=2)}\n\n"
        'Answer with exactly one JSON object and nothing else: {"same_visit": true} or '
        '{"same_visit": false}.'
    )


def _parse_same_visit(text: str) -> bool:
    """Extract the ``same_visit`` boolean from a model reply (first-``{`` to last-``}``).

    Raises on malformed/absent JSON or a missing/non-bool ``same_visit`` — the caller turns that
    into a single metered retry, then (on a second failure) a skip.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in tiebreak reply")
    payload = json.loads(text[start : end + 1])
    value = payload.get("same_visit")
    if not isinstance(value, bool):
        raise ValueError("tiebreak reply missing boolean 'same_visit'")
    return value


def _ask_tiebreak(client: MeteredLLMClient, a: MedicalEncounter, b: MedicalEncounter) -> bool:
    """Metered same-visit adjudication with one JSON-only retry (house parse-retry pattern)."""
    model = get_settings().merge_tiebreak_model
    prompt = _tiebreak_prompt(a, b)
    first = client.complete(stage=_TIEBREAK_STAGE, model=model, prompt=prompt)
    try:
        return _parse_same_visit(first.text)
    except (ValueError, json.JSONDecodeError):
        pass
    retry = client.complete(stage=_TIEBREAK_STAGE, model=model, prompt=prompt + _JSON_ONLY_SUFFIX)
    return _parse_same_visit(retry.text)


def merge_encounters(
    db: Session, client: MeteredLLMClient | None, *, matter: Matter
) -> MergeOutcome:
    """Merge duplicate/near-duplicate encounters for ``matter``. See module docstring for rules.

    ``client`` may be ``None`` (no tiebreak model available) — then every near-match pair is left
    unmerged and counted in ``tiebreaks_skipped`` (never guessed in code). Deterministic exact-key
    merges always run and need no client.
    """
    rows = _ordered_encounters(db, matter=matter)

    # Pass 1: deterministic exact-key collapse (no model).
    absorbed_ids, merged_groups = _merge_exact_key_groups(db, rows)
    survivors = [row for row in rows if row.id not in absorbed_ids]

    # Pass 2: LLM tiebreak for near-matches — same date, provider Jaccard ≥ 0.5, NOT exact-key
    # equal (those were already handled). Compare in deterministic order; a merged row drops out of
    # further comparison so we never chain a row into two survivors.
    llm_tiebreaks = 0
    tiebreaks_skipped = 0
    now_absorbed: set[uuid.UUID] = set()
    grew_via_llm: set[uuid.UUID] = set()
    for i, left in enumerate(survivors):
        if left.id in now_absorbed:
            continue
        for right in survivors[i + 1 :]:
            if right.id in now_absorbed:
                continue
            if left.date_of_service != right.date_of_service:
                continue
            if _exact_key(left) == _exact_key(right):
                continue  # exact-key equal is Pass-1 territory, not a tiebreak
            jaccard = _jaccard(_provider_tokens(left.provider), _provider_tokens(right.provider))
            if jaccard < _TIEBREAK_JACCARD_FLOOR:
                continue
            # A genuine near-match. Without a client, or if the model is unavailable / won't
            # parse, LEAVE UNMERGED and count — never guess a clinical-identity call in code.
            if client is None:
                tiebreaks_skipped += 1
                continue
            try:
                same_visit = _ask_tiebreak(client, left, right)
            except (
                ProviderNotConfigured,
                BudgetExceededError,
                ValueError,
                json.JSONDecodeError,
                ValidationError,
            ):
                tiebreaks_skipped += 1
                continue
            if same_visit:
                _absorb(db, survivor=left, absorbed=right, basis=MergeBasis.LLM_TIEBREAK)
                now_absorbed.add(right.id)
                llm_tiebreaks += 1
                if left.id not in grew_via_llm:
                    grew_via_llm.add(left.id)
                    merged_groups += 1
            else:
                tiebreaks_skipped += 1

    db.commit()

    remaining = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).count()
    return MergeOutcome(
        merged_groups=merged_groups,
        llm_tiebreaks=llm_tiebreaks,
        encounters_remaining=remaining,
        tiebreaks_skipped=tiebreaks_skipped,
    )
