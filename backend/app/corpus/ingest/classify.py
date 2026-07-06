"""Haiku document classification (component corpus_ingest §4 A3).

A cheap structured-output classification over a first-pages *text-layer* sample: the model
picks one of the closed :class:`~app.models.enums.DocType` values with a confidence score. Low
confidence or a degraded call (provider down, budget exhausted, unparseable reply) routes to the
review queue (``needs_review``) and defaults to ``other`` rather than guessing — every verdict is
recoverable via manual reclassification (:func:`reclassify_document`).

Two structural rules carry the corpus lesson:

* **Every model call goes through** :class:`~app.core.llm_telemetry.MeteredLLMClient` (invariant
  12) — there is no side door to the provider, so both the good attempt and the retry are metered.
* **A degraded classify still advances the lifecycle** (``status`` → ``classified``). Review is a
  queue the attorney works, not a stall that blocks the pipeline.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pdfplumber
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.llm_provider import ProviderNotConfigured
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.core.storage import ObjectStorage
from app.models.enums import DocStatus, DocType
from app.models.orm import CaseDocument, User
from app.models.schemas import ClassifierOutput

# The classifier stage id on the metering ledger.
_STAGE = "phase0.classify"
# Cap the sample handed to the model — a first-pages text layer is plenty to type a document, and
# an unbounded sample would blow the prompt budget on a long record.
_MAX_SAMPLE_CHARS = 4000
# One-phrase glosses for the closed vocabulary, so the model has the discriminating cue for each
# type without us hand-coding a classifier. Keys must stay in sync with DocType.
_DOC_TYPE_GLOSSES: dict[DocType, str] = {
    DocType.MEDICAL_RECORD: "clinical notes, encounter/visit records, imaging or lab reports",
    DocType.BILL: "an itemized medical bill, statement, or ledger of charges",
    DocType.POLICE_REPORT: "a police/incident/crash report from law enforcement",
    DocType.WAGE_DOC: "pay stubs, employer wage/earnings statements, lost-wage documentation",
    DocType.PHOTO: "a photograph (of the scene, vehicle, or injury) with little or no text",
    DocType.INSURANCE_CORR: "correspondence from an insurer (letters, claim notices, denials)",
    DocType.OTHER: "anything that fits none of the above",
}


@dataclass(frozen=True)
class ClassifyOutcome:
    """The result of a classify pass — what was written to the document, and how it went.

    ``doc_type`` is the value actually written (``other`` on any degrade or below-floor verdict).
    ``confidence`` is a score, not currency, so it is a float (or ``None`` when there is no model
    verdict to score). ``degrade_reason`` is one of ``provider_unavailable`` |
    ``budget_exceeded`` | ``parse_failed`` when ``degraded`` is set, else ``None``.
    """

    doc_type: str
    # Float is acceptable HERE ONLY: this is a classifier confidence score, not currency.
    confidence: float | None
    needs_review: bool
    degraded: bool
    degrade_reason: str | None


def sample_text_for(storage: ObjectStorage, document: CaseDocument, *, max_pages: int) -> str:
    """Return a text-LAYER-only sample of the document's first ``max_pages`` pages.

    Reads the stored blob through :class:`~app.core.storage.ObjectStorage`, opens it with
    ``pdfplumber``, and joins ``extract_text() or ""`` for the first ``max_pages`` pages with a
    form-feed. No OCR happens here — a scanned/image-only page legitimately yields ``""`` and the
    classifier degrades to review on an empty sample.

    ANY exception (a corrupt blob, a missing storage key, a non-PDF payload) is swallowed to
    ``""``: this stage only *samples* text. Marking a document failed on a bad blob is the pages
    stage's job, not the classifier's — degrading to review is the right, recoverable behaviour
    here.
    """
    if not document.storage_key:
        return ""
    try:
        blob = storage.get(document.storage_key)
        with pdfplumber.open(io.BytesIO(blob)) as pdf:
            pages = pdf.pages[:max_pages]
            return "\f".join((page.extract_text() or "") for page in pages)
    except Exception:
        # Sampling only — the pages stage owns failure marking; a bad blob degrades to review.
        return ""


def _build_prompt(*, filename: str, sample_text: str, insist_json: bool) -> str:
    """Assemble the classifier prompt: task, closed vocabulary, filename, then the sample.

    ``insist_json`` appends the retry suffix that demands a bare JSON object (used after a first
    reply failed to parse).
    """
    vocab_lines = "\n".join(
        f"- {doc_type.value}: {gloss}" for doc_type, gloss in _DOC_TYPE_GLOSSES.items()
    )
    sample = sample_text[:_MAX_SAMPLE_CHARS]
    prompt = (
        "You are classifying one document from a personal-injury case file. Choose the single "
        "best document type from this CLOSED list — use exactly one of these values:\n"
        f"{vocab_lines}\n\n"
        f"Filename: {filename}\n\n"
        "Document text sample (first pages, text layer only; may be empty for a scanned or "
        "image-only document):\n"
        "---\n"
        f"{sample}\n"
        "---\n\n"
        "Return exactly one JSON object and nothing else: "
        '{"doc_type": "<one of the values above>", "confidence": <number 0..1>, '
        '"rationale": "<short reason>"}'
    )
    if insist_json:
        prompt += "\n\nReturn ONLY the JSON object — no prose, no code fences."
    return prompt


def _parse_classifier_reply(text: str) -> ClassifierOutput:
    """Extract the JSON object from a model reply and validate it into a :class:`ClassifierOutput`.

    Tolerates surrounding prose/code fences by taking the substring from the first ``{`` to the
    last ``}`` before ``json.loads``. Raises on any malformed/absent JSON or a value outside the
    schema (e.g. an unknown ``doc_type``) — the caller turns that into a metered retry.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in classifier reply")
    payload = json.loads(text[start : end + 1])
    return ClassifierOutput.model_validate(payload)


def _run_classifier(
    client: MeteredLLMClient, *, filename: str, sample_text: str
) -> ClassifierOutput:
    """Call the metered client, retrying ONCE with a stricter prompt on a parse/validation failure.

    Both attempts go through the meter (that is correct — a wasted attempt is still a real call).
    Provider/budget errors are NOT caught here; they belong to the expected-offline paths in
    :func:`classify_document`. A second parse failure re-raises the parse error.
    """
    first = client.complete(
        stage=_STAGE,
        model=get_settings().classifier_model,
        prompt=_build_prompt(filename=filename, sample_text=sample_text, insist_json=False),
    )
    try:
        return _parse_classifier_reply(first.text)
    except (ValueError, json.JSONDecodeError):
        pass  # fall through to the single stricter retry
    retry = client.complete(
        stage=_STAGE,
        model=get_settings().classifier_model,
        prompt=_build_prompt(filename=filename, sample_text=sample_text, insist_json=True),
    )
    return _parse_classifier_reply(retry.text)


def _write_outcome(
    db: Session, document: CaseDocument, outcome: ClassifyOutcome
) -> ClassifyOutcome:
    """Persist an outcome onto the document and advance its lifecycle to ``classified``.

    A degraded classify still advances the lifecycle — review is the queue, not a stall.
    """
    document.doc_type = outcome.doc_type
    document.classification_confidence = outcome.confidence
    document.needs_review = outcome.needs_review
    document.status = DocStatus.CLASSIFIED.value
    db.commit()
    return outcome


def classify_document(
    db: Session,
    client: MeteredLLMClient,
    *,
    document: CaseDocument,
    sample_text: str,
) -> ClassifyOutcome:
    """Classify ``document`` from its ``sample_text`` and persist the verdict.

    * Provider unavailable (:class:`~app.core.llm_provider.ProviderNotConfigured`) or matter
      budget exhausted (:class:`~app.core.matter_budget.BudgetExceededError`) — the two EXPECTED
      offline conditions — degrade to ``(other, None, review, degraded)`` with the matching reason.
      Any OTHER exception propagates: unexpected failures stay loud.
    * Two parse/validation failures degrade to ``parse_failed``.
    * A good verdict at/above ``classifier_confidence_floor`` writes the model's ``doc_type`` with
      ``needs_review=False``; below the floor it routes to review as ``other`` (route-rather-than-
      guess) while keeping the returned confidence on the outcome AND the document.

    ALWAYS sets ``doc_type``, ``classification_confidence``, ``needs_review``, and ``status`` =
    ``classified`` and commits.
    """
    try:
        parsed = _run_classifier(client, filename=document.filename, sample_text=sample_text)
    except ProviderNotConfigured:
        return _write_outcome(
            db,
            document,
            ClassifyOutcome(DocType.OTHER.value, None, True, True, "provider_unavailable"),
        )
    except BudgetExceededError:
        return _write_outcome(
            db,
            document,
            ClassifyOutcome(DocType.OTHER.value, None, True, True, "budget_exceeded"),
        )
    except (ValueError, json.JSONDecodeError):
        # Both attempts failed to yield a valid ClassifierOutput.
        return _write_outcome(
            db,
            document,
            ClassifyOutcome(DocType.OTHER.value, None, True, True, "parse_failed"),
        )

    if parsed.confidence >= get_settings().classifier_confidence_floor:
        outcome = ClassifyOutcome(parsed.doc_type.value, parsed.confidence, False, False, None)
    else:
        # Below floor: route to review as `other` rather than guess, but keep the score.
        outcome = ClassifyOutcome(DocType.OTHER.value, parsed.confidence, True, False, None)
    return _write_outcome(db, document, outcome)


def reclassify_document(
    db: Session, *, user: User, document: CaseDocument, doc_type: DocType
) -> CaseDocument:
    """Apply an attorney's manual classification override and clear the review flag.

    ``classification_confidence`` is left as-is: it describes the LLM's verdict, not the human's,
    so a manual override does not manufacture a score. Writes a ``document_reclassified`` audit
    event (old + new type) and commits.
    """
    old_doc_type = document.doc_type
    document.doc_type = doc_type.value
    document.needs_review = False
    record_event(
        db,
        firm_id=document.firm_id,
        actor_id=user.id,
        event_kind="document_reclassified",
        payload={
            "document_id": str(document.id),
            "old_doc_type": old_doc_type,
            "new_doc_type": doc_type.value,
        },
    )
    db.commit()
    return document
