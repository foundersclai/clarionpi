"""Document classifier: metered structured-output, floor gating, degrade-to-review paths.

Every path is proven against the metering ledger (:class:`~app.models.orm.LlmCall`) — a good
classify writes exactly one row, a retry two, a pre-provider budget refusal zero — because the
one lesson that must be structural here is that no model call goes unmetered (invariant 12).

Tests build documents/pages directly via the ORM: this suite does NOT drive the page pipeline
(a parallel wave owns it), it only exercises classification over a supplied ``sample_text``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import load_or_create_budget
from app.core.storage import LocalDiskStorage
from app.corpus.ingest.classify import (
    ClassifyOutcome,
    classify_document,
    reclassify_document,
    sample_text_for,
)
from app.models.enums import DedupStatus, DocStatus, DocType
from app.models.orm import AuditEvent, CaseDocument, LlmCall, Matter, User


def _mk_doc(
    db: Session,
    matter: Matter,
    *,
    filename: str = "record.pdf",
    storage_key: str | None = "k/record.pdf",
    status: DocStatus = DocStatus.UPLOADED,
) -> CaseDocument:
    """Insert a CaseDocument directly (not via the page pipeline)."""
    doc = CaseDocument(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.OTHER.value,
        source_label=filename,
        filename=filename,
        storage_key=storage_key,
        page_count=0,
        dedup_status=DedupStatus.UNIQUE.value,
        status=status.value,
    )
    db.add(doc)
    db.commit()
    return doc


def _client(db: Session, matter: Matter, provider: ScriptedProvider) -> MeteredLLMClient:
    return MeteredLLMClient(provider, db, matter.firm_id, matter.id)


def _result(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=10, output_tokens=5, cost_cents=1)


def _ledger_count(db: Session, matter: Matter) -> int:
    return db.query(LlmCall).filter(LlmCall.matter_id == matter.id).count()


# --------------------------------------------------------------------------------------
# classify_document — happy, floor, degrade paths
# --------------------------------------------------------------------------------------


def test_valid_json_high_confidence_sets_type_and_meters_once(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    provider = ScriptedProvider(
        [_result('{"doc_type": "medical_record", "confidence": 0.93, "rationale": "notes"}')]
    )
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome == ClassifyOutcome("medical_record", 0.93, False, False, None)
    db.refresh(doc)
    assert doc.doc_type == DocType.MEDICAL_RECORD.value
    assert doc.needs_review is False
    assert doc.status == DocStatus.CLASSIFIED.value
    assert doc.classification_confidence == 0.93
    # Exactly one metering row — the structural proof no call is unmetered.
    assert _ledger_count(db, matter) == 1


def test_low_confidence_routes_to_review_as_other_but_keeps_score(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    provider = ScriptedProvider(
        [_result('{"doc_type": "medical_record", "confidence": 0.4, "rationale": "unsure"}')]
    )
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome == ClassifyOutcome("other", 0.4, True, False, None)
    db.refresh(doc)
    assert doc.doc_type == DocType.OTHER.value
    assert doc.needs_review is True
    assert doc.classification_confidence == 0.4
    assert doc.status == DocStatus.CLASSIFIED.value


def test_malformed_then_valid_converges_on_retry_two_ledger_rows(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    provider = ScriptedProvider(
        [
            _result("not json at all"),
            _result('{"doc_type": "bill", "confidence": 0.88, "rationale": "invoice"}'),
        ]
    )
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome.degraded is False
    assert outcome.doc_type == DocType.BILL.value
    assert outcome.needs_review is False
    # Both attempts are metered — a wasted attempt is still a real call.
    assert _ledger_count(db, matter) == 2
    assert len(provider.calls) == 2


def test_malformed_twice_degrades_parse_failed_two_ledger_rows(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    provider = ScriptedProvider([_result("garbage one"), _result("garbage two")])
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome == ClassifyOutcome("other", None, True, True, "parse_failed")
    db.refresh(doc)
    assert doc.doc_type == DocType.OTHER.value
    assert doc.needs_review is True
    assert doc.classification_confidence is None
    assert doc.status == DocStatus.CLASSIFIED.value
    assert _ledger_count(db, matter) == 2


def test_provider_unavailable_degrades_but_still_classified(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    # Empty script → ScriptedProvider raises ProviderNotConfigured on the first call.
    provider = ScriptedProvider([])
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome == ClassifyOutcome("other", None, True, True, "provider_unavailable")
    db.refresh(doc)
    assert doc.status == DocStatus.CLASSIFIED.value
    assert doc.needs_review is True


def test_budget_exhausted_degrades_and_provider_never_called(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    # Drive the matter to cap so the metered client refuses before the provider.
    budget = load_or_create_budget(db, firm_id=matter.firm_id, matter_id=matter.id)
    budget.spent_cents = budget.cap_cents
    db.commit()

    provider = ScriptedProvider(
        [_result('{"doc_type": "bill", "confidence": 0.9, "rationale": "x"}')]
    )
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome == ClassifyOutcome("other", None, True, True, "budget_exceeded")
    assert provider.calls == []  # provider must NOT have been invoked
    db.refresh(doc)
    assert doc.status == DocStatus.CLASSIFIED.value
    assert doc.needs_review is True


def test_json_embedded_in_prose_is_extracted(db: Session, dev_user: User, matter: Matter) -> None:
    doc = _mk_doc(db, matter)
    provider = ScriptedProvider(
        [
            _result(
                'Sure! {"doc_type": "police_report", "confidence": 0.81, '
                '"rationale": "crash"} hope that helps'
            )
        ]
    )
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome.doc_type == DocType.POLICE_REPORT.value
    assert outcome.needs_review is False
    assert _ledger_count(db, matter) == 1


def test_unknown_doc_type_value_retries_then_degrades(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    # "xray" is not a DocType member → validation fails → retry; second is also invalid → degrade.
    provider = ScriptedProvider(
        [
            _result('{"doc_type": "xray", "confidence": 0.95, "rationale": "img"}'),
            _result('{"doc_type": "still_invalid", "confidence": 0.95, "rationale": "img"}'),
        ]
    )
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome == ClassifyOutcome("other", None, True, True, "parse_failed")
    assert _ledger_count(db, matter) == 2


def test_confidence_at_floor_is_accepted(db: Session, dev_user: User, matter: Matter) -> None:
    # Default floor is 0.7; a verdict exactly at the floor is accepted (>=), not routed to review.
    doc = _mk_doc(db, matter)
    provider = ScriptedProvider(
        [_result('{"doc_type": "wage_doc", "confidence": 0.7, "rationale": "paystub"}')]
    )
    outcome = classify_document(db, _client(db, matter, provider), document=doc, sample_text="s")

    assert outcome.doc_type == DocType.WAGE_DOC.value
    assert outcome.needs_review is False


# --------------------------------------------------------------------------------------
# sample_text_for
# --------------------------------------------------------------------------------------


def test_sample_text_for_corrupt_blob_returns_empty(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    # A non-PDF ("corrupt") payload → pdfplumber raises → swallowed to "". No real PDF built here
    # (the pages wave owns PDF construction); this asserts the exception→"" contract.
    storage.put("k/corrupt.pdf", b"%PDF-1.4 this is not a real pdf body \x00\x01")
    doc = _mk_doc(db, matter, storage_key="k/corrupt.pdf")
    assert sample_text_for(storage, doc, max_pages=3) == ""


def test_sample_text_for_missing_key_returns_empty(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    doc = _mk_doc(db, matter, storage_key="k/never-stored.pdf")
    assert sample_text_for(storage, doc, max_pages=3) == ""


def test_sample_text_for_no_storage_key_returns_empty(
    db: Session, dev_user: User, matter: Matter, storage: LocalDiskStorage
) -> None:
    doc = _mk_doc(db, matter, storage_key=None)
    assert sample_text_for(storage, doc, max_pages=3) == ""


# --------------------------------------------------------------------------------------
# reclassify_document
# --------------------------------------------------------------------------------------


def test_reclassify_sets_type_clears_review_and_audits(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter)
    doc.doc_type = DocType.OTHER.value
    doc.needs_review = True
    doc.classification_confidence = 0.4
    db.commit()

    updated = reclassify_document(db, user=dev_user, document=doc, doc_type=DocType.MEDICAL_RECORD)

    assert updated.doc_type == DocType.MEDICAL_RECORD.value
    assert updated.needs_review is False
    # Confidence describes the LLM verdict, not the human override — left as-is.
    assert updated.classification_confidence == 0.4
    events = db.query(AuditEvent).filter(AuditEvent.event_kind == "document_reclassified").all()
    assert len(events) == 1
    assert events[0].payload["old_doc_type"] == DocType.OTHER.value
    assert events[0].payload["new_doc_type"] == DocType.MEDICAL_RECORD.value
    assert events[0].payload["document_id"] == str(doc.id)
