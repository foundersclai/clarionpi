"""The extraction runner — window-by-window, metered, anchor-validated (corpus_extraction §4).

:func:`extract_document` reads a document's ``DocumentPage`` rows, windows them, and for each
window runs the kind-appropriate extractor through the metered client, validates every emitted
anchor against the window that produced it, and persists the surviving rows. The invariants this
enforces (commented inline at each site):

* **Anti-fabrication (inv 2).** An anchor citing a page outside ``[window.start_page,
  window.end_page]`` means the model cited a page it was never shown — the whole row is DROPPED
  and counted, never persisted.
* **No arithmetic (money boundary).** Dollar strings normalize to cents ONLY through
  :func:`app.money.types.dollars_str_to_cents`; a value the parser refuses is dropped, never
  guessed into a number.
* **No semantic rewriting (inv 13).** Text fields are trimmed (whitespace, empties) — never
  reworded.
* **Metered door (inv 12).** Every model attempt, including the JSON-only retry, goes through
  :class:`~app.core.llm_telemetry.MeteredLLMClient`.
* **Idempotent + resumable.** A window with an ok/partial run is skipped; a failed run is deleted
  and retried; a provider/budget stop leaves no run rows for unprocessed windows so a re-run
  resumes exactly where it stopped.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.core.tenancy import tenant_add
from app.corpus.extraction import prompts
from app.corpus.extraction.windows import Window, build_windows
from app.models.enums import DocStatus, DocType, ExtractionStatus, ReconciliationStatus
from app.models.orm import (
    BillingLine,
    CaseDocument,
    DocumentPage,
    ExtractionRun,
    IncidentFacts,
    MedicalEncounter,
)
from app.models.schemas import (
    ExtractedBillingBatch,
    ExtractedEncounterBatch,
    ExtractedIncident,
    PageAnchor,
)
from app.money.types import MoneyParseError, dollars_str_to_cents


@dataclass(frozen=True)
class ExtractionOutcome:
    """Aggregate result of extracting one document across all its windows.

    ``rows_dropped_unparseable`` counts billing rows dropped because a money string the money
    parser refused (a separate concern from ``anchors_rejected``, which is out-of-window
    fabrication): a money value we cannot parse losslessly must never become a guessed number.
    ``skipped_reason`` is set (and every count is zero) only when the document was skipped whole.
    """

    runs_ok: int
    runs_partial: int
    runs_failed: int
    rows_emitted: int
    anchors_rejected: int
    rows_dropped_unparseable: int
    skipped_reason: str | None  # "doc_type_not_extractable" | "no_pages" | None


# Extractable doc types → (kind key for stage/prompt-version, prompt builder, batch schema).
# "other"/wage/photo/insurance docs are not extractable and are skipped visibly.
_KIND_BY_DOC_TYPE: dict[DocType, str] = {
    DocType.MEDICAL_RECORD: "medical",
    DocType.BILL: "bill",
    DocType.POLICE_REPORT: "police",
}

_PROMPT_BUILDERS: dict[str, Callable[[Window], str]] = {
    "medical": prompts.medical_prompt,
    "bill": prompts.bill_prompt,
    "police": prompts.police_prompt,
}

_BATCH_SCHEMA: dict[str, type[BaseModel]] = {
    "medical": ExtractedEncounterBatch,
    "bill": ExtractedBillingBatch,
    "police": ExtractedIncident,
}


def _summarize_parse_error(exc: Exception) -> str:
    """A short, diagnosable ``parse_failed`` reason (fits ``ExtractionRun.error``, 512 chars).

    A blind ``"parse_failed"`` hides WHICH field broke — the forbidden silent state. For a
    :class:`ValidationError` this names the first few offending ``loc:type`` pairs (e.g.
    ``lines.0.date_of_service:date_type``), which is what turns "extraction quietly dropped a
    bill" into an operator-actionable signal. Always prefixed ``parse_failed`` so existing
    reads that key on that prefix still match.
    """
    if isinstance(exc, ValidationError):
        pairs = [
            f"{'.'.join(str(p) for p in err['loc'])}:{err['type']}" for err in exc.errors()[:5]
        ]
        return f"parse_failed: {'; '.join(pairs)}"[:512]
    return f"parse_failed: {type(exc).__name__}: {exc}"[:512]


def _parse_json_object(text: str, schema: type[BaseModel]) -> BaseModel:
    """First-``{``-to-last-``}`` → ``json.loads`` → ``schema.model_validate`` (house pattern).

    Mirrors ``classify._parse_classifier_reply``: tolerate surrounding prose/code fences by
    slicing to the JSON object, then validate into the batch schema. Raises ``ValueError`` /
    ``json.JSONDecodeError`` / ``ValidationError`` on any malformed or off-schema reply — the
    caller turns that into a single metered retry.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in extractor reply")
    payload = json.loads(text[start : end + 1])
    return schema.model_validate(payload)


def _run_extractor(client: MeteredLLMClient, *, kind: str, window: Window) -> BaseModel:
    """Call the metered client for one window, retrying ONCE with a JSON-only suffix on parse fail.

    Both attempts go through the meter (a wasted attempt is still a real call, inv 12).
    Provider/budget errors are NOT caught here — they belong to the expected-offline handling in
    :func:`extract_document`. A second parse failure re-raises.
    """
    schema = _BATCH_SCHEMA[kind]
    stage = f"extract.{kind}"
    model = get_settings().extractor_model
    prompt = _PROMPT_BUILDERS[kind](window)

    first = client.complete(stage=stage, model=model, prompt=prompt)
    try:
        return _parse_json_object(first.text, schema)
    except (ValueError, json.JSONDecodeError, ValidationError):
        pass  # fall through to the single stricter retry
    retry = client.complete(stage=stage, model=model, prompt=prompt + prompts.JSON_ONLY_SUFFIX)
    return _parse_json_object(retry.text, schema)


def _anchors_in_window(pages: list[int], window: Window) -> bool:
    """True iff EVERY cited page lies inside the window's inclusive span.

    Any out-of-window page fails the whole row (anti-fabrication, inv 2): the model cited a page
    it was never shown.
    """
    return all(window.start_page <= p <= window.end_page for p in pages)


def _clean_list(values: list) -> list[str]:
    """Trim whitespace and drop empties from a list of strings — mechanical only (inv 13).

    No semantic rewriting: this is the whitespace/empty normalization §13 allows, nothing more.
    """
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def _anchor_dicts(document_id: uuid.UUID, window_id: str, pages: list[int]) -> list[dict]:
    """JSON-safe ``PageAnchor`` dicts for the given absolute pages (document_id + window_id)."""
    return [
        PageAnchor(document_id=document_id, page=p, window_id=window_id).model_dump(mode="json")
        for p in pages
    ]


@dataclass
class _WindowResult:
    """Per-window tallies the runner rolls up into an :class:`ExtractionOutcome`."""

    rows_emitted: int = 0
    anchors_rejected: int = 0
    rows_dropped_unparseable: int = 0


def _persist_encounters(
    db: Session,
    *,
    document: CaseDocument,
    window: Window,
    batch: ExtractedEncounterBatch,
) -> _WindowResult:
    """Persist medical encounters whose anchors are all in-window; drop + count fabricated rows."""
    result = _WindowResult()
    for enc in batch.encounters:
        if not _anchors_in_window(enc.anchor_pages, window):
            # Out-of-window page → fabricated cite → drop the whole row, never persist (inv 2).
            result.anchors_rejected += 1
            continue
        row = MedicalEncounter(
            matter_id=document.matter_id,
            date_of_service=enc.date_of_service,
            provider=enc.provider.strip(),
            facility=enc.facility.strip(),
            encounter_type=enc.encounter_type.strip(),
            complaints=_clean_list(enc.complaints),
            findings=_clean_list(enc.findings),
            diagnoses=_clean_list(enc.diagnoses),
            procedures=_clean_list(enc.procedures),
            work_status=enc.work_status,
            field_confidence=dict(enc.field_confidence),
            anchors=_anchor_dicts(document.id, window.window_id, enc.anchor_pages),
            merged_from=[],
            # No semantic rewriting here — the tokenizer wave (not this one) fills the narrative.
            narrative_tokenized="",
        )
        tenant_add(db, row, document.firm_id)
        result.rows_emitted += 1
    return result


def _persist_billing(
    db: Session,
    *,
    document: CaseDocument,
    window: Window,
    batch: ExtractedBillingBatch,
) -> _WindowResult:
    """Persist billing lines: anchor-validate, then money-parse each cell (drop-not-guess)."""
    result = _WindowResult()
    for line in batch.lines:
        if not _anchors_in_window([line.anchor_page], window):
            result.anchors_rejected += 1
            continue
        # Money boundary: normalize dollar strings ONLY via the money engine. A cell the parser
        # refuses drops the row and is counted separately — a money string we cannot parse
        # losslessly must never become a guessed number (distinct from anchor fabrication).
        try:
            billed_cents = dollars_str_to_cents(line.billed)
            adjusted_cents = None if line.adjusted is None else dollars_str_to_cents(line.adjusted)
            paid_cents = None if line.paid is None else dollars_str_to_cents(line.paid)
            outstanding_cents = (
                None if line.outstanding is None else dollars_str_to_cents(line.outstanding)
            )
        except MoneyParseError:
            result.rows_dropped_unparseable += 1
            continue
        anchor = PageAnchor(
            document_id=document.id, page=line.anchor_page, window_id=window.window_id
        ).model_dump(mode="json")
        row = BillingLine(
            matter_id=document.matter_id,
            provider=line.provider.strip(),
            date_of_service=line.date_of_service,
            service_end_date=line.service_end_date,
            code=line.code,
            billed_cents=billed_cents,
            adjusted_cents=adjusted_cents,
            paid_cents=paid_cents,
            outstanding_cents=outstanding_cents,
            category=line.category.value,
            reconciliation=ReconciliationStatus.LLM_ONLY.value,
            anchor=anchor,
        )
        tenant_add(db, row, document.firm_id)
        result.rows_emitted += 1
    return result


# Incident payload keys merged across windows (union of what each excerpt yielded).
_INCIDENT_SCALAR_KEYS = ("location", "incident_narrative")
_INCIDENT_LIST_KEYS = ("parties", "citations_issued")


def _persist_incident(
    db: Session,
    *,
    document: CaseDocument,
    window: Window,
    incident: ExtractedIncident,
) -> _WindowResult:
    """UPSERT the matter-unique ``IncidentFacts`` row, merging payload + unioning anchors.

    Multiple windows of a police report each contribute facts; they merge into ONE row per matter
    (the table is matter-unique). Non-empty new scalars win; list keys union; anchors dedup by
    (document_id, page).
    """
    result = _WindowResult()
    if not _anchors_in_window(incident.anchor_pages, window):
        result.anchors_rejected += 1
        return result

    new_anchors = _anchor_dicts(document.id, window.window_id, incident.anchor_pages)
    existing = (
        db.query(IncidentFacts).filter(IncidentFacts.matter_id == document.matter_id).one_or_none()
    )
    if existing is None:
        payload: dict[str, Any] = {
            "location": incident.location.strip(),
            "incident_narrative": incident.incident_narrative.strip(),
            "parties": list(incident.parties),
            "citations_issued": _clean_list(incident.citations_issued),
        }
        row = IncidentFacts(matter_id=document.matter_id, payload=payload, anchors=new_anchors)
        tenant_add(db, row, document.firm_id)
    else:
        merged: dict[str, Any] = dict(existing.payload)
        for key in _INCIDENT_SCALAR_KEYS:
            incoming = str(getattr(incident, key)).strip()
            # Prefer a non-empty new value; keep the existing one otherwise.
            if incoming:
                merged[key] = incoming
            else:
                merged.setdefault(key, "")
        # parties union (list of {name, role} dicts, dedup on the pair); citations union (strings).
        merged["parties"] = _union_dicts(list(merged.get("parties", [])), list(incident.parties))
        merged["citations_issued"] = _union_strs(
            list(merged.get("citations_issued", [])), _clean_list(incident.citations_issued)
        )
        existing.payload = merged
        existing.anchors = _union_anchor_dicts(list(existing.anchors), new_anchors)
    result.rows_emitted += 1
    return result


def _union_strs(existing: list, incoming: list[str]) -> list[str]:
    """Order-preserving union of two string lists."""
    out = list(existing)
    seen = set(existing)
    for value in incoming:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _union_dicts(existing: list, incoming: list[dict]) -> list[dict]:
    """Order-preserving union of {name, role} maps, deduped on the (name, role) pair."""
    out = list(existing)
    seen = {(d.get("name"), d.get("role")) for d in existing if isinstance(d, dict)}
    for value in incoming:
        key = (value.get("name"), value.get("role"))
        if key not in seen:
            out.append(value)
            seen.add(key)
    return out


def _union_anchor_dicts(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Union anchor dicts, deduped by (document_id, page)."""
    out = list(existing)
    seen = {(d.get("document_id"), d.get("page")) for d in existing}
    for anchor in incoming:
        key = (anchor.get("document_id"), anchor.get("page"))
        if key not in seen:
            out.append(anchor)
            seen.add(key)
    return out


def _persist_window(
    db: Session, *, kind: str, document: CaseDocument, window: Window, batch: BaseModel
) -> _WindowResult:
    """Dispatch persistence by kind."""
    if kind == "medical":
        assert isinstance(batch, ExtractedEncounterBatch)
        return _persist_encounters(db, document=document, window=window, batch=batch)
    if kind == "bill":
        assert isinstance(batch, ExtractedBillingBatch)
        return _persist_billing(db, document=document, window=window, batch=batch)
    assert kind == "police" and isinstance(batch, ExtractedIncident)
    return _persist_incident(db, document=document, window=window, incident=batch)


def _existing_run(
    db: Session, *, document_id: uuid.UUID, window_id: str, prompt_version: str
) -> ExtractionRun | None:
    """Fetch the run row for this (document, window, prompt_version) idempotency key, if any."""
    return (
        db.query(ExtractionRun)
        .filter(
            ExtractionRun.document_id == document_id,
            ExtractionRun.window_id == window_id,
            ExtractionRun.prompt_version == prompt_version,
        )
        .one_or_none()
    )


def extract_document(
    db: Session, client: MeteredLLMClient, *, document: CaseDocument
) -> ExtractionOutcome:
    """Extract typed facts from ``document`` window-by-window; persist survivors; set doc status.

    See the module docstring for the enforced invariants. Returns an :class:`ExtractionOutcome`
    tallying per-window run statuses, persisted rows, and dropped rows (fabricated anchors vs
    unparseable money). Unexpected exceptions propagate; the two EXPECTED offline conditions
    (provider down, budget exhausted) stop the doc mid-run and leave it resumable.
    """
    # Kind by doc_type. A non-extractable type (incl. a degraded-classified `other`) is skipped
    # VISIBLY with no writes — reclassifying to an extractable type lets a re-run pick it up.
    doc_type = DocType(document.doc_type)
    kind = _KIND_BY_DOC_TYPE.get(doc_type)
    if kind is None:
        return ExtractionOutcome(0, 0, 0, 0, 0, 0, "doc_type_not_extractable")

    prompt_version = prompts.PROMPT_VERSIONS[kind]
    settings = get_settings()

    pages = db.query(DocumentPage).filter(DocumentPage.document_id == document.id).all()
    if not pages:
        return ExtractionOutcome(0, 0, 0, 0, 0, 0, "no_pages")

    windows = build_windows(
        pages,
        size=settings.extraction_window_pages,
        overlap=settings.extraction_window_overlap,
    )

    runs_ok = 0
    runs_partial = 0
    runs_failed = 0
    total_rows = 0
    total_rejected = 0
    total_unparseable = 0
    # Doc reaches EXTRACTED only if EVERY window ended ok/partial. Any failed/missing window
    # leaves it OCR_DONE (re-runnable).
    all_windows_done = True

    for window in windows:
        # Idempotency FIRST: an existing ok/partial run for this key is a no-op (re-entrancy). A
        # failed run is deleted and the window retried fresh (so a transient failure self-heals).
        prior = _existing_run(
            db,
            document_id=document.id,
            window_id=window.window_id,
            prompt_version=prompt_version,
        )
        if prior is not None:
            if prior.status in (ExtractionStatus.OK.value, ExtractionStatus.PARTIAL.value):
                if prior.status == ExtractionStatus.OK.value:
                    runs_ok += 1
                else:
                    runs_partial += 1
                continue
            # status == failed → delete and retry this window fresh.
            db.delete(prior)
            db.flush()

        try:
            batch = _run_extractor(client, kind=kind, window=window)
        except (ProviderNotConfigured, BudgetExceededError) as exc:
            # Expected offline conditions: record THIS window failed, then STOP — remaining
            # windows would fail identically, and leaving NO run rows for them lets a later
            # re-run resume exactly here (idempotency).
            error = (
                "budget_exceeded"
                if isinstance(exc, BudgetExceededError)
                else "provider_unavailable"
            )
            run = ExtractionRun(
                matter_id=document.matter_id,
                document_id=document.id,
                window_id=window.window_id,
                window_start=window.start_page,
                window_end=window.end_page,
                prompt_version=prompt_version,
                model=settings.extractor_model,
                status=ExtractionStatus.FAILED.value,
                error=error,
                rows_emitted=0,
                anchors_rejected=0,
            )
            tenant_add(db, run, document.firm_id)
            db.commit()
            runs_failed += 1
            all_windows_done = False
            return ExtractionOutcome(
                runs_ok,
                runs_partial,
                runs_failed,
                total_rows,
                total_rejected,
                total_unparseable,
                None,
            )
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            # Two parse failures on THIS window: record it FAILED and continue to the next window
            # — one bad window must not kill the doc. The doc-level status rule keeps it OCR_DONE.
            # Record WHICH field/shape broke (not a blind "parse_failed") so a silently dropped
            # bill becomes an operator-diagnosable signal, never an invisible zero.
            run = ExtractionRun(
                matter_id=document.matter_id,
                document_id=document.id,
                window_id=window.window_id,
                window_start=window.start_page,
                window_end=window.end_page,
                prompt_version=prompt_version,
                model=settings.extractor_model,
                status=ExtractionStatus.FAILED.value,
                error=_summarize_parse_error(exc),
                rows_emitted=0,
                anchors_rejected=0,
            )
            tenant_add(db, run, document.firm_id)
            db.commit()
            runs_failed += 1
            all_windows_done = False
            continue

        # Validate anchors + persist survivors. A window with any dropped row is PARTIAL.
        wr = _persist_window(db, kind=kind, document=document, window=window, batch=batch)
        dropped = wr.anchors_rejected + wr.rows_dropped_unparseable
        status = ExtractionStatus.OK.value if dropped == 0 else ExtractionStatus.PARTIAL.value
        run = ExtractionRun(
            matter_id=document.matter_id,
            document_id=document.id,
            window_id=window.window_id,
            window_start=window.start_page,
            window_end=window.end_page,
            prompt_version=prompt_version,
            model=settings.extractor_model,
            status=status,
            error=None,
            rows_emitted=wr.rows_emitted,
            anchors_rejected=wr.anchors_rejected,
        )
        tenant_add(db, run, document.firm_id)
        db.commit()  # one commit per window run (rows + run row together)

        if status == ExtractionStatus.OK.value:
            runs_ok += 1
        else:
            runs_partial += 1
        total_rows += wr.rows_emitted
        total_rejected += wr.anchors_rejected
        total_unparseable += wr.rows_dropped_unparseable

    # Doc-level status: EXTRACTED iff every window has an ok/partial run (all_windows_done stays
    # True only if no window failed and there was ≥1 window). Otherwise leave OCR_DONE.
    if all_windows_done and windows:
        document.status = DocStatus.EXTRACTED.value
        db.commit()

    return ExtractionOutcome(
        runs_ok,
        runs_partial,
        runs_failed,
        total_rows,
        total_rejected,
        total_unparseable,
        None,
    )
