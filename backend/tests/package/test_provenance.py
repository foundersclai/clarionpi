"""Provenance report tests — the E4 audit trail (M5 Wave B2).

Self-contained (own in-memory engine + firm/attorney/matter, direct ORM), matching
``tests/package/test_manifest.py``'s fixture style. FactTokens are minted through the registry
(the only minter) so spans resolve to real display forms/sources; DraftSection rows + their spans
are hand-built (no drafter, no brain2 import). Synthetic data only — no PHI.

Coverage: the completeness property (report fact-entry count == total spans across sections, no
span dropped or duplicated — asserted by counting the rendered "[TOKEN]" lines in the PDF text);
an orphan span renders the sentinel + orphan outcome; the omitted-adverse section lists the
omit_with_rationale flag + its rationale; the Part-3 judgment-call section lists an OVERRIDE
finding; byte determinism.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import uuid
from collections.abc import Iterator

import pytest
from pypdf import PdfReader
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.tenancy import tenant_add
from app.engine.tokenizer import registry
from app.engine.tokenizer.registry import SENTINEL
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    FindingBucket,
    FindingDisposition,
    FindingGating,
    FindingStatus,
    FlagDisposition,
    FlagKind,
    FlagSeverity,
    GateState,
    SectionValidation,
    UserRole,
)
from app.models.orm import (
    CaseDocument,
    ComplianceFinding,
    DemandDraft,
    DraftSection,
    Firm,
    Matter,
    MedicalEncounter,
    RiskFlag,
    User,
)
from app.package import provenance as prov

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=2500,
        )
    )
    create_all_for_tests(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def firm(db: Session) -> Firm:
    f = Firm(id=uuid.uuid4(), name="Test Firm")
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def attorney(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="atty@firm.test",
        display_name="Attorney",
        role=UserRole.ATTORNEY.value,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def matter(db: Session, firm: Firm) -> Matter:
    m_ = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Jane Doe",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.PACKAGE_ASSEMBLY.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m_)
    db.commit()
    return m_


def _mint_two_facts(db: Session, matter: Matter) -> tuple[str, str, uuid.UUID]:
    """Mint two extractor FACT tokens (a doc + two encounters); return the bare ids + doc id."""
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type=DocType.MEDICAL_RECORD.value,
        source_label="records.pdf",
        filename="records.pdf",
        storage_key="blobs/records.pdf",
        page_count=3,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.EXTRACTED.value,
    )
    db.add(doc)
    for provider, day in (("Dr. Alice", 10), ("Dr. Bob", 20)):
        enc = MedicalEncounter(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            date_of_service=dt.date(2026, 1, day),
            provider=provider,
            facility="General",
            encounter_type="ER",
            complaints=[],
            findings=[],
            diagnoses=[],
            procedures=[],
            work_status=None,
            narrative_tokenized="",
            anchors=[{"document_id": str(doc.id), "page": 1}],
            merged_from=[],
            field_confidence={},
        )
        db.add(enc)
    db.commit()
    registry.sync_extracted_facts(db, matter=matter)  # FACT_1, FACT_2
    return "FACT_1", "FACT_2", doc.id


def _draft(db: Session, matter: Matter) -> DemandDraft:
    d = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        strategy_plan_version=1,
        status="approved",
        memo="",
    )
    db.add(d)
    db.flush()
    return d


def _section(
    db: Session, draft: DemandDraft, matter: Matter, *, section_id: str, spans: list[dict]
) -> DraftSection:
    s = DraftSection(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        purpose="p",
        body_tokenized="x",
        rendered_preview="rendered",
        registry_version=matter.registry_version,
        validation=SectionValidation.PASSED.value,
        spans=spans,
        sort_order=1,
    )
    db.add(s)
    db.commit()
    return s


def _span(token_id: str, start: int, end: int) -> dict:
    return {"span_id": str(uuid.uuid4()), "start": start, "end": end, "token_id": token_id}


def _pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


# --------------------------------------------------------------------------------------
# Part 1 — rendered facts + completeness
# --------------------------------------------------------------------------------------


def test_provenance_completeness_entries_equal_span_count(db: Session, matter: Matter) -> None:
    f1, f2, _doc = _mint_two_facts(db, matter)
    draft = _draft(db, matter)
    # Three spans across two sections (one repeats FACT_1 — still counted once per span).
    s1 = _section(
        db, draft, matter, section_id="liability", spans=[_span(f1, 0, 5), _span(f2, 6, 10)]
    )
    s2 = _section(db, draft, matter, section_id="damages", spans=[_span(f1, 0, 5)])

    data = prov.build_provenance_report(db, matter=matter, draft=draft, sections=[s1, s2], flags=[])
    text = _pdf_text(data)
    # The report prints "Total rendered facts: N"; N must equal the total span count (3).
    m = re.search(r"Total rendered facts:\s*(\d+)", text)
    assert m is not None
    assert int(m.group(1)) == 3
    # And a per-span "[TOKEN_ID]" line appears for each span (FACT_1 twice, FACT_2 once).
    assert text.count("[FACT_1]") == 2
    assert text.count("[FACT_2]") == 1


def test_provenance_ok_span_shows_source(db: Session, matter: Matter) -> None:
    f1, _f2, _doc = _mint_two_facts(db, matter)
    draft = _draft(db, matter)
    s1 = _section(db, draft, matter, section_id="liability", spans=[_span(f1, 0, 5)])
    text = _pdf_text(
        prov.build_provenance_report(db, matter=matter, draft=draft, sections=[s1], flags=[])
    )
    # An extractor-sourced, verified fact renders "(ok; source: extractor)".
    assert "ok; source: extractor" in text


def test_provenance_orphan_span_shows_sentinel_and_outcome(db: Session, matter: Matter) -> None:
    draft = _draft(db, matter)
    # A span whose token was never minted -> orphan.
    s1 = _section(db, draft, matter, section_id="liability", spans=[_span("FACT_99", 0, 5)])
    text = _pdf_text(
        prov.build_provenance_report(db, matter=matter, draft=draft, sections=[s1], flags=[])
    )
    assert SENTINEL in text
    assert "orphan" in text
    assert "source: —" in text  # no FactToken row -> em-dash source


# --------------------------------------------------------------------------------------
# Part 2 — adverse facts omitted with rationale
# --------------------------------------------------------------------------------------


def test_provenance_lists_omitted_adverse_with_rationale(
    db: Session, matter: Matter, attorney: User
) -> None:
    draft = _draft(db, matter)
    flag = RiskFlag(
        matter_id=matter.id,
        kind=FlagKind.PREEXISTING_CONDITION.value,
        severity=FlagSeverity.HIGH.value,
        anchors=[],
        detail="prior neck injury noted in 2024 records",
        disposition=FlagDisposition.OMIT_WITH_RATIONALE.value,
        disposition_role=UserRole.ATTORNEY.value,
        disposition_rationale="unrelated to this collision; different anatomy",
    )
    tenant_add(db, flag, matter.firm_id)
    db.commit()

    text = _pdf_text(
        prov.build_provenance_report(db, matter=matter, draft=draft, sections=[], flags=[flag])
    )
    assert "preexisting_condition" in text
    assert "prior neck injury" in text
    assert "unrelated to this collision" in text


def test_provenance_lists_need_more_records_as_open_items(db: Session, matter: Matter) -> None:
    draft = _draft(db, matter)
    flag = RiskFlag(
        matter_id=matter.id,
        kind=FlagKind.TREATMENT_GAP.value,
        severity=FlagSeverity.MEDIUM.value,
        anchors=[],
        detail="6-week gap between ER and PT",
        disposition=FlagDisposition.NEED_MORE_RECORDS.value,
    )
    tenant_add(db, flag, matter.firm_id)
    db.commit()
    text = _pdf_text(
        prov.build_provenance_report(db, matter=matter, draft=draft, sections=[], flags=[flag])
    )
    assert "Open items" in text
    assert "treatment_gap" in text


def test_provenance_no_omitted_adverse_says_none(db: Session, matter: Matter) -> None:
    draft = _draft(db, matter)
    text = _pdf_text(
        prov.build_provenance_report(db, matter=matter, draft=draft, sections=[], flags=[])
    )
    # Part 2 header present with a "None recorded." under it.
    assert "Adverse facts omitted" in text
    assert "None recorded." in text


# --------------------------------------------------------------------------------------
# Part 3 — judgment calls (OVERRIDE findings)
# --------------------------------------------------------------------------------------


def test_provenance_lists_override_findings(db: Session, matter: Matter) -> None:
    draft = _draft(db, matter)
    finding = ComplianceFinding(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id="damages",
        registry_version=matter.registry_version,
        check_kind="tone",
        bucket=FindingBucket.SEMANTIC.value,
        severity=FindingGating.ADVISORY.value,
        detail="tone flagged as slightly aggressive",
        anchors=[],
        status=FindingStatus.DISPOSITIONED.value,
        disposition=FindingDisposition.OVERRIDE.value,
        override_reason="attorney accepts the tone; the demand is firm by design",
    )
    tenant_add(db, finding, matter.firm_id)
    db.commit()
    text = _pdf_text(
        prov.build_provenance_report(db, matter=matter, draft=draft, sections=[], flags=[])
    )
    assert "Judgment calls" in text
    assert "tone" in text
    assert "attorney accepts the tone" in text


def test_provenance_no_override_findings_says_none(db: Session, matter: Matter) -> None:
    draft = _draft(db, matter)
    text = _pdf_text(
        prov.build_provenance_report(db, matter=matter, draft=draft, sections=[], flags=[])
    )
    assert "Judgment calls" in text
    # Both Part-2-empty and Part-3-empty print "None recorded."; assert at least the judgment one.
    assert text.count("None recorded.") >= 2


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_provenance_is_byte_deterministic(db: Session, matter: Matter) -> None:
    f1, f2, _doc = _mint_two_facts(db, matter)
    draft = _draft(db, matter)
    s1 = _section(
        db, draft, matter, section_id="liability", spans=[_span(f1, 0, 5), _span(f2, 6, 9)]
    )
    a = prov.build_provenance_report(db, matter=matter, draft=draft, sections=[s1], flags=[])
    b = prov.build_provenance_report(db, matter=matter, draft=draft, sections=[s1], flags=[])
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()
