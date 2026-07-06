"""The chronology builder — derived rows, a first-class overlay store, tokens-only narratives.

This module is Brain-1's assembly surface (chronology_builder / system_contract §2, 5, 10). It
turns the matter's already-extracted, already-tokenized :class:`~app.models.orm.MedicalEncounter`
rows into an attorney-review chronology, and it does so under three structural rules:

* **Rows are DERIVED, never persisted (inv 10).** :func:`build_chronology` rebuilds the whole
  chronology from the encounters on every call; a :class:`ChronologyRow` is an in-memory value
  object. ``row_id == str(encounter_id)`` so a paralegal's overlay re-keys cleanly across
  rebuilds — the id never reflows.

* **Overlays survive rebuilds and are never silently dropped (chronology_builder §3).** A
  :class:`~app.models.orm.ChronologyRowOverlay` is keyed by its encounter. On rebuild the builder
  compares the overlay's ``base_hash_at_edit`` against a freshly computed :func:`base_hash_for`:
  a match lays the edit over the base row (``APPLIED``); a mismatch means the base drifted under
  the edit, so the overlay is quarantined (``CONFLICT``) with BOTH versions visible and never
  auto-resolved; an overlay whose encounter has vanished (a merge absorbed it) is
  ``PARKED_ORPHANED`` — parked, not deleted, because paralegal work is recoverable.

* **Narratives are tokens-only (inv 5).** The per-encounter generator refers to a visit ONLY by
  its registry ``[[FACT_n]]`` token; the raw provider name and date render *from the token*, they
  are never restated in the prose. The generator writes into
  ``MedicalEncounter.narrative_tokenized`` and **this module is the one documented writer of that
  column**. Generation is per-encounter and isolated — never a whole-chronology regen — and every
  narrative passes a deterministic validation GATE (inv 13: a gate, not a code-side normalizer)
  before it persists.

The chronology does **no arithmetic** and **mints no tokens**: it composes the landed tokenizer
(:mod:`app.engine.tokenizer.registry`) for resolution and the metered client
(:class:`~app.core.llm_telemetry.MeteredLLMClient`) for generation. A build RETURNS its
zero-unregistered-claims scan (the M2 exit criterion); it does not raise on a dirty narrative —
that is a G3 block downstream.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.core.tenancy import tenant_add
from app.engine.tokenizer import registry
from app.models.enums import OverlayStatus
from app.models.orm import ChronologyRowOverlay, FactToken, Matter, MedicalEncounter, User

_LOG = logging.getLogger("clarionpi.chronology")

# The generator stage id on the metering ledger.
_NARRATIVE_STAGE = "chronology.narrative"

# The audit event kind written by an overlay upsert.
_OVERLAY_AUDIT_KIND = "chronology_overlay_upserted"


# --------------------------------------------------------------------------------------
# Row + outcome value objects
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChronologyRow:
    """A DERIVED chronology row — never persisted; rebuilt from encounters every time (inv 10).

    ``row_id == str(encounter_id)`` so it is stable across rebuilds and overlays re-key cleanly.
    ``narrative_tokenized`` is carried from the encounter row (persisted there — this module is
    that column's single writer). ``base_hash`` is the overlay-conflict detector (see
    :func:`base_hash_for`). ``overlay_status`` is ``None`` when no overlay touched this row, else
    the :class:`~app.models.enums.OverlayStatus` value that applied. ``effective_fields`` is the
    base field dict with an ``APPLIED`` overlay's ``edited_fields`` laid over it (base only, when
    the overlay conflicts / is orphaned / is absent).
    """

    row_id: str
    date_of_service: date
    provider_display: str
    facility_display: str
    encounter_type: str
    narrative_tokenized: str
    anchors: tuple[dict, ...]
    base_hash: str
    overlay_status: str | None
    effective_fields: dict


@dataclass(frozen=True)
class ChronologyBuildOutcome:
    """The result of one :func:`build_chronology` pass — rows plus exact accounting.

    ``unregistered_claims`` MUST be empty on a healthy build (the M2 exit criterion): every token
    in every row's narrative resolves in the registry. A build returns them (loud ERROR-logged);
    the eval asserts empty and G3 blocks on them later — the build itself does not raise.
    """

    rows: tuple[ChronologyRow, ...]
    narratives_generated: int
    narratives_skipped: int
    narratives_failed: int
    overlays_applied: int
    overlays_conflict: int
    overlays_parked: int
    unregistered_claims: tuple[str, ...]


# --------------------------------------------------------------------------------------
# Base hash — the overlay-conflict detector
# --------------------------------------------------------------------------------------


def base_hash_for(encounter: MedicalEncounter) -> str:
    """SHA-256 over the deterministic tuple of an encounter's BASE inputs.

    The digest covers ``(date_of_service iso, provider, facility, encounter_type,
    tuple(complaints), tuple(findings), tuple(diagnoses), tuple(procedures), work_status,
    narrative_tokenized)``. Changing ANY base input changes the hash, so an overlay minted against
    an older base is detected as a ``CONFLICT`` on the next rebuild. Order-stable and
    JSON-canonical (``sort_keys`` off — the tuple order IS the contract) so the same inputs always
    hash the same.
    """
    payload = [
        encounter.date_of_service.isoformat(),
        encounter.provider,
        encounter.facility,
        encounter.encounter_type,
        list(encounter.complaints),
        list(encounter.findings),
        list(encounter.diagnoses),
        list(encounter.procedures),
        encounter.work_status,
        encounter.narrative_tokenized,
    ]
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _base_fields(encounter: MedicalEncounter) -> dict:
    """The base field dict for a row's ``effective_fields`` — the encounter's business fields."""
    return {
        "date_of_service": encounter.date_of_service.isoformat(),
        "provider": encounter.provider,
        "facility": encounter.facility,
        "encounter_type": encounter.encounter_type,
        "complaints": list(encounter.complaints),
        "findings": list(encounter.findings),
        "diagnoses": list(encounter.diagnoses),
        "procedures": list(encounter.procedures),
        "work_status": encounter.work_status,
    }


def _anchor_dicts(anchors: object) -> list[dict]:
    """Normalize a row's ``anchors`` JSON to a list of plain dicts (defensive copy)."""
    if not isinstance(anchors, list):
        return []
    return [dict(a) for a in anchors if isinstance(a, dict)]


# --------------------------------------------------------------------------------------
# Narrative generation (per-encounter, isolated)
# --------------------------------------------------------------------------------------


def _encounter_fact_token(
    db: Session, *, matter: Matter, encounter: MedicalEncounter
) -> str | None:
    """The full FACT token (``[[FACT_n]]``) for an encounter, or ``None`` if none exists yet.

    Finds the encounter's registry row by ``source_ref == f"encounter:{id}"`` at its LATEST
    version (mirroring registry ``_latest`` semantics), then renders its ``token_id`` back into a
    full token. No row -> ``None`` (sync has not run for this encounter) -> the caller skips
    generation rather than minting here (this module never mints).
    """
    source_ref = f"encounter:{encounter.id}"
    rows = list(
        db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id,
                FactToken.source_ref == source_ref,
            )
        ).scalars()
    )
    if not rows:
        return None
    latest = max(rows, key=lambda r: r.registry_version)
    return f"[[{latest.token_id}]]"


def _clinical_lines(encounter: MedicalEncounter) -> str:
    """The clinical CONTENT the generator may summarize — the encounter's typed lists."""

    def _fmt(label: str, items: Sequence[object]) -> str:
        rendered = ", ".join(str(i) for i in items) if items else "(none recorded)"
        return f"- {label}: {rendered}"

    lines = [
        _fmt("Complaints", encounter.complaints),
        _fmt("Findings", encounter.findings),
        _fmt("Diagnoses", encounter.diagnoses),
        _fmt("Procedures", encounter.procedures),
        f"- Work status: {encounter.work_status or '(none recorded)'}",
    ]
    return "\n".join(lines)


def _build_narrative_prompt(
    *, token: str, display_form: str, encounter: MedicalEncounter, violation: str | None
) -> str:
    """Assemble the narrative prompt: the token to use, its display form, the clinical content.

    The prompt hands the model the token id STRING and its display form (so it knows what the
    token stands for), plus the encounter's clinical lists (the content it may summarize). It is
    instructed to refer to the visit ONLY as ``<token>`` — never restating the provider name or
    the date, which render from the token — and never to state a dollar amount. ``violation``, when
    set, names the specific rule the previous attempt broke so the single regeneration is targeted.
    """
    prompt = (
        "You are writing one row of a medical chronology for a personal-injury demand package.\n\n"
        f"The visit is represented by the fact token {token}. It stands for: "
        f"{display_form!r}.\n"
        "The provider name and date of service are already carried by that token and will be "
        "rendered from it — you must NOT restate the provider's name or the date in your text.\n\n"
        "Clinical content you may summarize (this is the substance of the visit):\n"
        f"{_clinical_lines(encounter)}\n\n"
        "Write 1 to 3 sentences summarizing this visit for the chronology. Rules:\n"
        f"- Refer to the visit ONLY as {token} (use that exact token at least once); never write "
        "the provider's name or the date of service.\n"
        "- Do NOT state any dollar amount or figure.\n"
        "- Use only the clinical content above; do not invent facts.\n\n"
        'Return exactly one JSON object and nothing else: {"narrative": "<your 1-3 sentences>"}'
    )
    if violation is not None:
        prompt += (
            "\n\nYour previous attempt was rejected for this reason: "
            f"{violation}\n"
            f"Fix it. Return ONLY the JSON object; refer to the visit as {token}; do not name the "
            "provider or the date; do not state any dollar amount."
        )
    return prompt


def _parse_narrative(text: str) -> str:
    """Extract the ``narrative`` string from a model reply (house JSON pattern).

    Tolerates surrounding prose / code fences by taking the substring from the first ``{`` to the
    last ``}`` before ``json.loads``. Raises ``ValueError`` on malformed/absent JSON or a missing/
    non-string ``narrative`` — the caller treats that like a validation violation (it feeds the
    single regeneration budget), so a parse miss can never leak an unvalidated narrative.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in narrative reply")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("narrative reply JSON is not an object")
    narrative = payload.get("narrative")
    if not isinstance(narrative, str):
        raise ValueError("narrative reply missing a string 'narrative' field")
    return narrative


def _validate_narrative(
    db: Session,
    *,
    matter: Matter,
    encounter: MedicalEncounter,
    token: str,
    narrative: str,
) -> str | None:
    """Deterministic tokens-only GATE (inv 13). Returns a violation string, or ``None`` if clean.

    Checks, in order (first failure named):

    * the encounter's own ``token`` appears at least once (the visit must be referenced by token);
    * every token-shaped substring resolves in the registry (``scan_unregistered`` is empty) — a
      narrative citing a slot the registry never minted is a fabricated reference;
    * the raw provider string does not appear (casefolded containment) — it must render from the
      token, not be restated;
    * the date of service in ISO form does not appear — same reason.

    This is a gate, not a normalizer: it never edits the narrative, it only accepts or rejects.
    """
    if token not in narrative:
        return f"the narrative must reference the visit as {token} at least once"
    unregistered = registry.scan_unregistered(db, matter=matter, text=narrative)
    if unregistered:
        return (
            "the narrative contains token(s) that do not resolve in the registry: "
            f"{', '.join(unregistered)}"
        )
    provider = encounter.provider.strip()
    if provider and provider.casefold() in narrative.casefold():
        return (
            "the narrative must not restate the provider name "
            f"({provider!r}); refer to the visit as {token} instead"
        )
    dos_iso = encounter.date_of_service.isoformat()
    if dos_iso in narrative:
        return (
            f"the narrative must not restate the date of service ({dos_iso}); "
            f"refer to the visit as {token} instead"
        )
    return None


def _generate_narrative(
    db: Session,
    client: MeteredLLMClient,
    *,
    matter: Matter,
    encounter: MedicalEncounter,
    token: str,
    display_form: str,
) -> str | None:
    """Generate + validate one encounter's narrative. Returns the clean narrative, or ``None``.

    At most TWO metered calls: attempt 1, and — if it fails to parse OR fails validation — one
    regeneration naming the violation. (The house "one parse retry" and the "one validation
    regeneration" collapse into this single second attempt, so a bad first reply for any reason
    costs exactly one extra call.) Both attempts dirty -> ``None`` (the caller counts
    ``narratives_failed`` and leaves the column empty; the row still builds). Provider/budget
    errors are NOT caught here — they belong to the caller's expected-offline stop path.
    """
    model = get_settings().narrative_model
    violation: str | None = None
    for attempt in range(2):
        prompt = _build_narrative_prompt(
            token=token, display_form=display_form, encounter=encounter, violation=violation
        )
        result = client.complete(stage=_NARRATIVE_STAGE, model=model, prompt=prompt)
        try:
            narrative = _parse_narrative(result.text)
        except (ValueError, json.JSONDecodeError) as exc:
            violation = str(exc)
            _LOG.warning(
                "narrative parse failed for encounter %s (attempt %d): %s",
                encounter.id,
                attempt + 1,
                exc,
            )
            continue
        violation = _validate_narrative(
            db, matter=matter, encounter=encounter, token=token, narrative=narrative
        )
        if violation is None:
            return narrative
        _LOG.warning(
            "narrative validation rejected encounter %s (attempt %d): %s",
            encounter.id,
            attempt + 1,
            violation,
        )
    return None


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------


def _load_encounters(db: Session, *, matter: Matter) -> list[MedicalEncounter]:
    """The matter's encounters ordered ``(date_of_service, created_at, id)`` — deterministic.

    The stable tiebreak (created_at then id) means the chronology never randomly reflows two
    same-day encounters between rebuilds (chronology_builder §4).
    """
    return list(
        db.execute(
            select(MedicalEncounter)
            .where(MedicalEncounter.matter_id == matter.id)
            .order_by(
                MedicalEncounter.date_of_service,
                MedicalEncounter.created_at,
                MedicalEncounter.id,
            )
        ).scalars()
    )


def _generate_all_narratives(
    db: Session,
    client: MeteredLLMClient,
    *,
    matter: Matter,
    encounters: Sequence[MedicalEncounter],
) -> tuple[int, int, int]:
    """Fill empty ``narrative_tokenized`` columns, per-encounter and isolated.

    Returns ``(generated, skipped, failed)``. On the first ``ProviderNotConfigured`` /
    ``BudgetExceededError`` the loop STOPS attempting and counts every REMAINING empty-narrative
    encounter as skipped (visible degradation, never a silent stall). A missing FACT token (sync
    has not run for that encounter) is also a skip — this module never mints. A clean narrative is
    written to the encounter and committed per-encounter (isolation: one bad row can't roll back a
    good one).
    """
    generated = 0
    skipped = 0
    failed = 0
    provider_down = False

    for enc in encounters:
        if enc.narrative_tokenized:
            continue  # already generated — never regenerate a non-empty narrative
        if provider_down:
            skipped += 1
            continue

        token = _encounter_fact_token(db, matter=matter, encounter=enc)
        if token is None:
            skipped += 1
            _LOG.info(
                "no FACT token yet for encounter %s (sync not run); skipping narrative", enc.id
            )
            continue

        display_form = registry.resolve_for_prompt(db, matter=matter, token=token)
        try:
            narrative = _generate_narrative(
                db, client, matter=matter, encounter=enc, token=token, display_form=display_form
            )
        except (ProviderNotConfigured, BudgetExceededError) as exc:
            # Expected-offline stop: skip THIS and every remaining encounter, visibly.
            provider_down = True
            skipped += 1
            _LOG.warning(
                "narrative generation unavailable at encounter %s (%s); skipping the rest",
                enc.id,
                type(exc).__name__,
            )
            continue

        if narrative is None:
            failed += 1
            _LOG.error(
                "narrative generation failed validation twice for encounter %s; leaving empty",
                enc.id,
            )
            continue

        enc.narrative_tokenized = narrative
        db.add(enc)
        db.commit()  # per-encounter commit — isolation (chronology_builder §4)
        generated += 1

    return generated, skipped, failed


def _overlays_by_encounter(db: Session, *, matter: Matter) -> dict[str, ChronologyRowOverlay]:
    """All of the matter's overlays, indexed by ``str(encounter_id)`` (the row key)."""
    rows = list(
        db.execute(
            select(ChronologyRowOverlay).where(ChronologyRowOverlay.matter_id == matter.id)
        ).scalars()
    )
    return {str(row.encounter_id): row for row in rows}


def build_chronology(
    db: Session,
    client: MeteredLLMClient | None,
    *,
    matter: Matter,
    generate_narratives: bool = True,
) -> ChronologyBuildOutcome:
    """Build the matter's derived chronology: rows + narratives + overlay reapply.

    Steps (see the module docstring for the invariants each upholds):

    1. Load encounters ordered ``(date_of_service, created_at, id)`` — deterministic, stable.
    2. Narrative generation (only when ``generate_narratives`` and ``client is not None``):
       per-encounter, isolated, tokens-only, validated by a deterministic gate; a missing token or
       an offline provider is a visible skip, a twice-invalid narrative is a visible failure — the
       row still builds either way.
    3. Rows: ``base_hash_for`` each; ``effective_fields`` = base, with an ``APPLIED`` overlay's
       ``edited_fields`` laid over it when the overlay's ``base_hash_at_edit`` matches; a hash
       mismatch quarantines the overlay as ``CONFLICT`` (base wins, both versions visible, never
       auto-resolved). Overlay status changes are persisted.
    4. Orphan sweep: overlays whose encounter no longer exists become ``PARKED_ORPHANED`` (parked,
       never deleted).
    5. Zero-unregistered-claims scan (M2 exit): ``scan_unregistered`` over every row's narrative,
       collected into ``unregistered_claims`` (ERROR-logged per token). RETURNED, not raised.
    6. Commit; return the outcome.
    """
    encounters = _load_encounters(db, matter=matter)

    narratives_generated = 0
    narratives_skipped = 0
    narratives_failed = 0
    if generate_narratives and client is not None:
        narratives_generated, narratives_skipped, narratives_failed = _generate_all_narratives(
            db, client, matter=matter, encounters=encounters
        )

    overlays = _overlays_by_encounter(db, matter=matter)
    live_encounter_ids: set[str] = set()

    rows: list[ChronologyRow] = []
    overlays_applied = 0
    overlays_conflict = 0
    overlays_parked = 0

    for enc in encounters:
        row_id = str(enc.id)
        live_encounter_ids.add(row_id)
        base_hash = base_hash_for(enc)
        effective_fields = _base_fields(enc)
        overlay_status: str | None = None

        overlay = overlays.get(row_id)
        if overlay is not None:
            if overlay.base_hash_at_edit == base_hash:
                # Base unchanged since the edit — lay the paralegal's edits over the base.
                effective_fields.update(dict(overlay.edited_fields))
                overlay_status = OverlayStatus.APPLIED.value
                overlays_applied += 1
            else:
                # Base drifted under the edit — quarantine, never auto-resolve. Base wins in the
                # row; the overlay's edits stay visible on the overlay row for G2a.
                overlay_status = OverlayStatus.CONFLICT.value
                overlays_conflict += 1
            if overlay.status != overlay_status:
                overlay.status = overlay_status
                db.add(overlay)

        rows.append(
            ChronologyRow(
                row_id=row_id,
                date_of_service=enc.date_of_service,
                provider_display=enc.provider,
                facility_display=enc.facility,
                encounter_type=enc.encounter_type,
                narrative_tokenized=enc.narrative_tokenized,
                anchors=tuple(_anchor_dicts(enc.anchors)),
                base_hash=base_hash,
                overlay_status=overlay_status,
                effective_fields=effective_fields,
            )
        )

    # Orphan sweep: an overlay whose encounter is gone (a merge absorbed it) is parked, not
    # deleted — paralegal work is recoverable.
    for enc_id, overlay in overlays.items():
        if enc_id in live_encounter_ids:
            continue
        if overlay.status != OverlayStatus.PARKED_ORPHANED.value:
            overlay.status = OverlayStatus.PARKED_ORPHANED.value
            db.add(overlay)
        overlays_parked += 1

    # Zero-unregistered-claims scan (the M2 exit criterion) — RETURNED, not raised.
    unregistered: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not row.narrative_tokenized:
            continue
        for token in registry.scan_unregistered(db, matter=matter, text=row.narrative_tokenized):
            if token in seen:
                continue
            seen.add(token)
            unregistered.append(token)
            _LOG.error(
                "unregistered token %s in chronology narrative for matter %s (row %s)",
                token,
                matter.id,
                row.row_id,
            )

    db.commit()
    return ChronologyBuildOutcome(
        rows=tuple(rows),
        narratives_generated=narratives_generated,
        narratives_skipped=narratives_skipped,
        narratives_failed=narratives_failed,
        overlays_applied=overlays_applied,
        overlays_conflict=overlays_conflict,
        overlays_parked=overlays_parked,
        unregistered_claims=tuple(unregistered),
    )


# --------------------------------------------------------------------------------------
# Overlay upsert + wire rendering
# --------------------------------------------------------------------------------------


def upsert_overlay(
    db: Session,
    *,
    user: User,
    matter: Matter,
    encounter: MedicalEncounter,
    edited_fields: dict,
) -> ChronologyRowOverlay:
    """Create or update the ``(matter, encounter)`` overlay carrying a paralegal's row edit.

    ``edited_fields`` is replaced wholesale (an overlay is the full set of a row's edits, not a
    patch history). ``base_hash_at_edit`` is pinned to :func:`base_hash_for` computed NOW, so a
    later base drift is detectable as a ``CONFLICT``; ``status`` is set ``APPLIED`` (the edit takes
    effect against the current base) and the actor is recorded. Writes a
    ``chronology_overlay_upserted`` audit event via :func:`app.core.audit.record_event` and
    commits. Returns the row.
    """
    base_hash = base_hash_for(encounter)
    overlay = db.execute(
        select(ChronologyRowOverlay).where(
            ChronologyRowOverlay.matter_id == matter.id,
            ChronologyRowOverlay.encounter_id == encounter.id,
        )
    ).scalar_one_or_none()

    created = overlay is None
    if overlay is None:
        overlay = ChronologyRowOverlay(
            matter_id=matter.id,
            encounter_id=encounter.id,
            edited_fields=dict(edited_fields),
            base_hash_at_edit=base_hash,
            status=OverlayStatus.APPLIED.value,
            actor_id=user.id,
        )
        tenant_add(db, overlay, matter.firm_id)
    else:
        overlay.edited_fields = dict(edited_fields)
        overlay.base_hash_at_edit = base_hash
        overlay.status = OverlayStatus.APPLIED.value
        overlay.actor_id = user.id
        db.add(overlay)

    record_event(
        db,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind=_OVERLAY_AUDIT_KIND,
        payload={
            "matter_id": str(matter.id),
            "encounter_id": str(encounter.id),
            "created": created,
            "edited_field_keys": sorted(edited_fields.keys()),
            "base_hash_at_edit": base_hash,
        },
    )
    db.commit()
    return overlay


def render_rows_for_wire(
    db: Session, *, matter: Matter, rows: Sequence[ChronologyRow]
) -> list[dict]:
    """View-layer helper: render each derived row to a wire-safe dict.

    Each row's ``narrative_tokenized`` passes through
    :func:`app.engine.tokenizer.registry.resolve_text_for_wire` — tokens become their display
    forms, an orphan becomes the sentinel, and NOTHING token-shaped survives (inv 11; the helper
    itself asserts this). The dict carries ``row_id``, ``date_of_service`` (ISO), the display
    provider/facility, ``encounter_type``, the RENDERED ``narrative``, ``anchors``, and
    ``overlay_status``.
    """
    out: list[dict] = []
    for row in rows:
        narrative = registry.resolve_text_for_wire(db, matter=matter, text=row.narrative_tokenized)
        out.append(
            {
                "row_id": row.row_id,
                "date_of_service": row.date_of_service.isoformat(),
                "provider_display": row.provider_display,
                "facility_display": row.facility_display,
                "encounter_type": row.encounter_type,
                "narrative": narrative,
                "anchors": [dict(a) for a in row.anchors],
                "overlay_status": row.overlay_status,
            }
        )
    return out
