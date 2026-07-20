"""Extraction runner tests: happy paths, anti-fabrication, money parse, resumability, metering.

Every path is proven against the metering ledger (:class:`~app.models.orm.LlmCall`) and the
persisted rows. Documents/pages are built directly via the ORM — this wave does not drive the
page pipeline. The anti-fabrication test (an anchor citing a page outside the window) is the
point of the wave and is asserted loud and exact.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import load_or_create_budget
from app.corpus.extraction.runner import extract_document
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    ExtractionStatus,
    ReconciliationStatus,
    TextSource,
)
from app.models.orm import (
    BillingLine,
    CaseDocument,
    DocumentPage,
    ExtractionRun,
    IncidentFacts,
    LlmCall,
    Matter,
    MedicalEncounter,
    User,
)

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _client(db: Session, matter: Matter, provider: ScriptedProvider) -> MeteredLLMClient:
    return MeteredLLMClient(provider, db, matter.firm_id, matter.id)


def _result(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=10, output_tokens=5, cost_cents=1)


def _ledger_count(db: Session, matter: Matter) -> int:
    return db.query(LlmCall).filter(LlmCall.matter_id == matter.id).count()


def _mk_doc(
    db: Session,
    matter: Matter,
    *,
    doc_type: DocType,
    n_pages: int,
    status: DocStatus = DocStatus.OCR_DONE,
) -> CaseDocument:
    """Insert a CaseDocument + ``n_pages`` DocumentPage rows directly (no page pipeline)."""
    doc = CaseDocument(
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=doc_type.value,
        source_label="doc.pdf",
        filename="doc.pdf",
        storage_key="k/doc.pdf",
        page_count=n_pages,
        dedup_status=DedupStatus.UNIQUE.value,
        status=status.value,
    )
    db.add(doc)
    db.flush()
    for page_no in range(1, n_pages + 1):
        db.add(
            DocumentPage(
                firm_id=matter.firm_id,
                document_id=doc.id,
                page_no=page_no,
                text=f"page {page_no} text",
                text_source=TextSource.TEXT_LAYER.value,
            )
        )
    db.commit()
    return doc


def _encounter_json(*, page: int, provider: str = "Dr. Smith", dos: str = "2026-02-01") -> str:
    return (
        '{"encounters": [{'
        f'"date_of_service": "{dos}", "provider": "{provider}", '
        '"facility": "  Mercy General  ", "encounter_type": "office visit", '
        '"complaints": ["neck pain", "  ", "neck pain"], "findings": ["tenderness"], '
        '"diagnoses": ["strain"], "procedures": [], "work_status": "light duty", '
        f'"anchor_pages": [{page}], '
        '"field_confidence": {"provider": 0.9, "date_of_service": 0.8}'
        "}]}"
    )


# --------------------------------------------------------------------------------------
# medical happy path
# --------------------------------------------------------------------------------------


def test_medical_two_windows_persists_encounters_with_anchors_and_sets_extracted(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # 10 pages, default window (size 8 overlap 2) → windows [1-8], [3-10]: two windows.
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=10)
    provider = ScriptedProvider(
        [
            _result(_encounter_json(page=2, provider="Dr. Smith", dos="2026-02-01")),
            _result(_encounter_json(page=9, provider="Dr. Jones", dos="2026-03-01")),
        ]
    )
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.skipped_reason is None
    assert outcome.runs_ok == 2
    assert outcome.runs_partial == 0
    assert outcome.runs_failed == 0
    assert outcome.rows_emitted == 2
    assert outcome.anchors_rejected == 0

    encounters = (
        db.query(MedicalEncounter)
        .filter(MedicalEncounter.matter_id == matter.id)
        .order_by(MedicalEncounter.date_of_service)
        .all()
    )
    assert [e.provider for e in encounters] == ["Dr. Smith", "Dr. Jones"]
    first = encounters[0]
    # Anchor carries document_id + ABSOLUTE page + window_id.
    assert len(first.anchors) == 1
    assert first.anchors[0]["document_id"] == str(doc.id)
    assert first.anchors[0]["page"] == 2
    assert first.anchors[0]["window_id"] == f"{doc.id}:1-8"
    # Mechanical trims only: facility stripped, empty complaint dropped, dup collapsed on clean.
    assert first.facility == "Mercy General"
    assert first.complaints == ["neck pain", "neck pain"]  # whitespace-only dropped, not deduped
    assert first.field_confidence == {"provider": 0.9, "date_of_service": 0.8}
    assert first.narrative_tokenized == ""
    assert first.merged_from == []

    # Two ExtractionRuns, both ok, rows_emitted=1 each.
    runs = db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).all()
    assert len(runs) == 2
    assert all(r.status == ExtractionStatus.OK.value for r in runs)
    assert sum(r.rows_emitted for r in runs) == 2

    db.refresh(doc)
    assert doc.status == DocStatus.EXTRACTED.value
    assert _ledger_count(db, matter) == 2


# --------------------------------------------------------------------------------------
# anti-fabrication (THE point of the wave)
# --------------------------------------------------------------------------------------


def test_anchor_outside_window_is_dropped_counted_and_run_partial(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # Single window [1-3]. The model cites page 99 — a page it was NEVER shown.
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=3)
    provider = ScriptedProvider([_result(_encounter_json(page=99))])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    # The fabricated row is dropped, counted, and NEVER persisted.
    assert outcome.anchors_rejected == 1
    assert outcome.rows_emitted == 0
    assert db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).count() == 0

    # The run is PARTIAL (some rows dropped), and the doc still reaches EXTRACTED (the window ran).
    run = db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).one()
    assert run.status == ExtractionStatus.PARTIAL.value
    assert run.anchors_rejected == 1
    assert run.rows_emitted == 0
    db.refresh(doc)
    assert doc.status == DocStatus.EXTRACTED.value


def test_mixed_valid_and_fabricated_rows_persists_only_the_valid_one(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=3)
    batch = (
        '{"encounters": ['
        '{"date_of_service": "2026-02-01", "provider": "Dr. In", "facility": "", '
        '"encounter_type": "visit", "complaints": [], "findings": [], "diagnoses": [], '
        '"procedures": [], "work_status": null, "anchor_pages": [2], "field_confidence": {}},'
        '{"date_of_service": "2026-02-02", "provider": "Dr. Out", "facility": "", '
        '"encounter_type": "visit", "complaints": [], "findings": [], "diagnoses": [], '
        '"procedures": [], "work_status": null, "anchor_pages": [50], "field_confidence": {}}'
        "]}"
    )
    provider = ScriptedProvider([_result(batch)])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.rows_emitted == 1
    assert outcome.anchors_rejected == 1
    rows = db.query(MedicalEncounter).filter(MedicalEncounter.matter_id == matter.id).all()
    assert [r.provider for r in rows] == ["Dr. In"]


# --------------------------------------------------------------------------------------
# bills: dollar-string parsing + drop-not-guess + single anchor
# --------------------------------------------------------------------------------------


def test_bills_parse_dollar_strings_to_exact_cents(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.BILL, n_pages=3)
    batch = (
        '{"lines": ['
        '{"provider": "Imaging Co", "date_of_service": "2026-02-01", "code": "70450", '
        '"billed": "$1,234.56", "adjusted": "1,234.56", "paid": "$0.07", "outstanding": null, '
        '"category": "imaging", "anchor_page": 1}'
        "]}"
    )
    provider = ScriptedProvider([_result(batch)])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.rows_emitted == 1
    assert outcome.rows_dropped_unparseable == 0
    line = db.query(BillingLine).filter(BillingLine.matter_id == matter.id).one()
    assert line.billed_cents == 123456
    assert line.adjusted_cents == 123456
    assert line.paid_cents == 7
    assert line.outstanding_cents is None  # absent → None, not guessed
    assert line.category == "imaging"
    assert line.reconciliation == ReconciliationStatus.LLM_ONLY.value
    # Single anchor dict (not a list) with document_id + absolute page + window_id.
    assert isinstance(line.anchor, dict)
    assert line.anchor["document_id"] == str(doc.id)
    assert line.anchor["page"] == 1


def test_bill_row_with_unparseable_money_is_dropped_and_counted_others_persist(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.BILL, n_pages=3)
    batch = (
        '{"lines": ['
        '{"provider": "Good", "date_of_service": "2026-02-01", "code": null, '
        '"billed": "$100.00", "adjusted": null, "paid": null, "outstanding": null, '
        '"category": "er", "anchor_page": 1},'
        '{"provider": "Bad", "date_of_service": "2026-02-02", "code": null, '
        '"billed": "$1,2X4.00", "adjusted": null, "paid": null, "outstanding": null, '
        '"category": "er", "anchor_page": 1}'
        "]}"
    )
    provider = ScriptedProvider([_result(batch)])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    # A money string the parser refuses is a SEPARATE concern from anchor fabrication.
    assert outcome.rows_dropped_unparseable == 1
    assert outcome.anchors_rejected == 0
    assert outcome.rows_emitted == 1
    rows = db.query(BillingLine).filter(BillingLine.matter_id == matter.id).all()
    assert [r.provider for r in rows] == ["Good"]
    # A dropped row makes the run PARTIAL.
    run = db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).one()
    assert run.status == ExtractionStatus.PARTIAL.value


def test_bill_service_period_persists_start_and_end(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # A range-header bill: the period line carries date_of_service (start) + service_end_date
    # (end); an ordinary single-date line leaves service_end_date null. The honest period is
    # recorded, not collapsed to a single-day fiction.
    doc = _mk_doc(db, matter, doc_type=DocType.BILL, n_pages=3)
    batch = (
        '{"lines": ['
        '{"provider": "Desert Sky Ortho", "date_of_service": "2025-03-24", '
        '"service_end_date": "2025-06-16", "code": null, "billed": "$1,290.00", '
        '"adjusted": null, "paid": null, "outstanding": null, "category": "ortho", '
        '"anchor_page": 1},'
        '{"provider": "Saguaro ER", "date_of_service": "2025-03-14", '
        '"service_end_date": null, "code": "99284", "billed": "$9,200.00", '
        '"adjusted": null, "paid": null, "outstanding": null, "category": "er", '
        '"anchor_page": 1}'
        "]}"
    )
    provider = ScriptedProvider([_result(batch)])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.rows_emitted == 2
    rows = {
        r.provider: r
        for r in db.query(BillingLine).filter(BillingLine.matter_id == matter.id).all()
    }
    # Period line: start anchors date_of_service (every sort/consumer keeps a non-null date),
    # end is the honest range end.
    assert rows["Desert Sky Ortho"].date_of_service.isoformat() == "2025-03-24"
    assert rows["Desert Sky Ortho"].service_end_date is not None
    assert rows["Desert Sky Ortho"].service_end_date.isoformat() == "2025-06-16"
    # Single-date line: no distinct end.
    assert rows["Saguaro ER"].date_of_service.isoformat() == "2025-03-14"
    assert rows["Saguaro ER"].service_end_date is None


def test_bill_null_date_of_service_fails_loud_naming_the_field(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # The exact observed bug: a bill line with no per-line date the model refuses to guess emits
    # date_of_service=null → ValidationError. It STILL fails (we do not silently zero-fill), but
    # the failure is now diagnosable — the run's error names the offending field rather than a
    # blind "parse_failed". This is the no-silent-state guard, model-free and deterministic.
    doc = _mk_doc(db, matter, doc_type=DocType.BILL, n_pages=3)
    null_date_line = (
        '{"lines": ['
        '{"provider": "Cactus Valley PT", "date_of_service": null, '
        '"service_end_date": null, "code": "97110", "billed": "$3,540.00", '
        '"adjusted": null, "paid": null, "outstanding": null, "category": "pt_chiro", '
        '"anchor_page": 1}'
        "]}"
    )
    # Both attempts (first + JSON-only retry) return the same off-schema shape.
    provider = ScriptedProvider([_result(null_date_line), _result(null_date_line)])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.runs_failed == 1
    assert outcome.rows_emitted == 0
    run = db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).one()
    assert run.status == ExtractionStatus.FAILED.value
    assert run.error.startswith("parse_failed")
    # The field is named — this is what makes "$3,540 quietly missing" impossible to miss.
    assert "date_of_service" in run.error
    db.refresh(doc)
    assert doc.status == DocStatus.OCR_DONE.value  # a failed window leaves the doc re-runnable


# --------------------------------------------------------------------------------------
# incident: two windows upsert ONE IncidentFacts row
# --------------------------------------------------------------------------------------


def test_incident_two_windows_upsert_one_row_merging_payload_and_anchors(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.POLICE_REPORT, n_pages=10)  # two windows
    win1 = (
        '{"location": "Main St & 1st Ave", "incident_narrative": "Rear-end collision.", '
        '"parties": [{"name": "Driver A", "role": "driver"}], '
        '"citations_issued": ["fail to yield"], "anchor_pages": [1]}'
    )
    win2 = (
        '{"location": "", "incident_narrative": "", '
        '"parties": [{"name": "Witness B", "role": "witness"}], '
        '"citations_issued": ["speeding"], "anchor_pages": [9]}'
    )
    provider = ScriptedProvider([_result(win1), _result(win2)])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.runs_ok == 2
    facts = db.query(IncidentFacts).filter(IncidentFacts.matter_id == matter.id).all()
    assert len(facts) == 1  # matter-unique: ONE row across both windows
    row = facts[0]
    # Non-empty scalars from window 1 survive (window 2 sent empties).
    assert row.payload["location"] == "Main St & 1st Ave"
    assert row.payload["incident_narrative"] == "Rear-end collision."
    # Lists union across windows.
    names = {p["name"] for p in row.payload["parties"]}
    assert names == {"Driver A", "Witness B"}
    assert set(row.payload["citations_issued"]) == {"fail to yield", "speeding"}
    # Anchors unioned across both windows.
    pages = {a["page"] for a in row.anchors}
    assert pages == {1, 9}


# --------------------------------------------------------------------------------------
# skip paths
# --------------------------------------------------------------------------------------


def test_non_extractable_doc_type_is_skipped_visibly_with_no_writes(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.OTHER, n_pages=3)
    provider = ScriptedProvider([])  # must never be called
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.skipped_reason == "doc_type_not_extractable"
    assert (outcome.runs_ok, outcome.runs_partial, outcome.runs_failed) == (0, 0, 0)
    assert provider.calls == []
    assert db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).count() == 0
    db.refresh(doc)
    assert doc.status == DocStatus.OCR_DONE.value  # unchanged


def test_document_with_no_pages_is_skipped(db: Session, dev_user: User, matter: Matter) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=0)
    provider = ScriptedProvider([])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.skipped_reason == "no_pages"
    assert provider.calls == []


# --------------------------------------------------------------------------------------
# resumability: provider unavailable mid-doc
# --------------------------------------------------------------------------------------


def test_provider_unavailable_mid_doc_stops_and_resumes_on_rerun(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # 10 pages → two windows [1-8],[7-10] (default size 8 overlap 2, step 6). Window 1 succeeds;
    # the script is then exhausted, so window 2's completion raises ProviderNotConfigured.
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=10)
    provider1 = ScriptedProvider([_result(_encounter_json(page=2))])
    outcome1 = extract_document(db, _client(db, matter, provider1), document=doc)

    assert outcome1.runs_ok == 1
    assert outcome1.runs_failed == 1
    # Window 1 got an ok run; window 2 got a FAILED provider_unavailable run; no run for anything
    # past the stop point (there is nothing past window 2 here, but the failed row is present).
    runs = {
        r.window_id: r
        for r in db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).all()
    }
    assert runs[f"{doc.id}:1-8"].status == ExtractionStatus.OK.value
    assert runs[f"{doc.id}:7-10"].status == ExtractionStatus.FAILED.value
    assert runs[f"{doc.id}:7-10"].error == "provider_unavailable"
    db.refresh(doc)
    assert doc.status == DocStatus.OCR_DONE.value  # NOT extracted — a window failed

    # Re-run with a fresh client: window 1's ok run is a no-op; the failed window-2 run is deleted
    # and retried fresh, now succeeding. Idempotency proof: only window 2 is re-extracted.
    provider2 = ScriptedProvider([_result(_encounter_json(page=9))])
    outcome2 = extract_document(db, _client(db, matter, provider2), document=doc)

    assert len(provider2.calls) == 1  # ONLY window 2 re-extracted, not window 1
    assert outcome2.runs_ok == 2  # window 1 (skipped-as-ok) + window 2 (fresh ok)
    assert outcome2.runs_failed == 0
    final_runs = db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).all()
    assert all(r.status == ExtractionStatus.OK.value for r in final_runs)
    db.refresh(doc)
    assert doc.status == DocStatus.EXTRACTED.value


def test_budget_exhausted_mid_doc_records_failed_and_stops(
    db: Session, dev_user: User, matter: Matter
) -> None:
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=10)
    # Drive the matter to cap so the FIRST window's completion is refused before the provider.
    budget = load_or_create_budget(db, firm_id=matter.firm_id, matter_id=matter.id)
    budget.spent_cents = budget.cap_cents
    db.commit()

    provider = ScriptedProvider([_result(_encounter_json(page=2))])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.runs_failed == 1
    assert outcome.runs_ok == 0
    assert provider.calls == []  # budget refusal happens before the provider is called
    run = db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).all()
    # Only the first window has a run row (FAILED budget_exceeded); the rest are untouched.
    assert len(run) == 1
    assert run[0].status == ExtractionStatus.FAILED.value
    assert run[0].error == "budget_exceeded"
    db.refresh(doc)
    assert doc.status == DocStatus.OCR_DONE.value


# --------------------------------------------------------------------------------------
# parse failure twice on one window
# --------------------------------------------------------------------------------------


def test_parse_fail_twice_on_one_window_is_failed_others_unaffected(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # 10 pages → two windows. Window 1: two unparseable replies → FAILED parse_failed.
    # Window 2: a valid batch → ok. One bad window must not kill the doc, but the doc stays
    # OCR_DONE because a window failed.
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=10)
    provider = ScriptedProvider(
        [
            _result("garbage one"),
            _result("garbage two"),
            _result(_encounter_json(page=9)),
        ]
    )
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.runs_failed == 1
    assert outcome.runs_ok == 1
    runs = {
        r.window_id: r
        for r in db.query(ExtractionRun).filter(ExtractionRun.document_id == doc.id).all()
    }
    assert runs[f"{doc.id}:1-8"].status == ExtractionStatus.FAILED.value
    # The reason is diagnosable, not a blind "parse_failed": it keeps the prefix AND carries the
    # underlying cause so a silently dropped window is operator-actionable (no-silent-state).
    failed_error = runs[f"{doc.id}:1-8"].error
    assert failed_error.startswith("parse_failed")
    assert "no JSON object" in failed_error
    assert runs[f"{doc.id}:7-10"].status == ExtractionStatus.OK.value
    # Both attempts on window 1 + one on window 2 = 3 metered calls.
    assert _ledger_count(db, matter) == 3
    db.refresh(doc)
    assert doc.status == DocStatus.OCR_DONE.value


# --------------------------------------------------------------------------------------
# metering: ledger rows match completes (incl. retries)
# --------------------------------------------------------------------------------------


def test_retry_on_first_window_is_metered_twice(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # 3 pages → one window. First reply malformed, retry valid → two metered calls, one row.
    doc = _mk_doc(db, matter, doc_type=DocType.MEDICAL_RECORD, n_pages=3)
    provider = ScriptedProvider([_result("not json"), _result(_encounter_json(page=1))])
    outcome = extract_document(db, _client(db, matter, provider), document=doc)

    assert outcome.rows_emitted == 1
    assert outcome.runs_ok == 1
    assert len(provider.calls) == 2
    assert _ledger_count(db, matter) == 2  # ledger rows == completes (incl. the retry)
