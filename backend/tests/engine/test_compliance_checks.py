"""Deterministic G3 check tests (M5 Wave C) — one planted fixture per :class:`CheckKind`.

Self-contained in-memory engine + firm/user/matter parked at ``compliance_review``. Tokens are
minted via the real registry so the checks run against production token shapes; billing lines,
documents, exhibits, and risk flags are seeded directly. Synthetic data only — no PHI.

Coverage: orphan (fake token id in a body); AMT-ledger mismatch (a billing edit after mint moves
the live hash); dead anchor two ways (a page_count shrink AND a dedup-superseded document); a
missing exhibit (an EX token minted but absent from the manifest); a prose-total mismatch (a
literal ``$9,999.99`` in a rendered preview); an undisposed adverse flag; and a fully clean draft
→ zero findings.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.engine.compliance.checks import build_check_context, run_deterministic_checks
from app.engine.tokenizer import registry
from app.models.enums import CheckKind, GateState
from app.models.orm import (
    BillingLine,
    CaseDocument,
    DedupDecision,
    DemandDraft,
    DraftSection,
    Exhibit,
    Firm,
    Matter,
    RiskFlag,
    User,
)
from app.models.schemas import PageAnchor
from app.money.assemble import compute_matter_ledger
from app.rules.loader import load_pack

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=100000,
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
def user(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="attorney@firm.test",
        display_name="Test Attorney",
        role="attorney",
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def matter(db: Session, firm: Firm) -> Matter:
    m = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Jane Doe",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.COMPLIANCE_REVIEW.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()
    return m


def _mint_fact(db: Session, matter: Matter, user: User, display: str, anchors=()) -> str:
    row = registry.mint_attorney_fact(
        db,
        matter=matter,
        user=user,
        display_form=display,
        value={"note": display},
        anchors=anchors,
    )
    db.refresh(matter)
    return row.token_id


def _document(db: Session, matter: Matter, *, filename: str, page_count: int) -> CaseDocument:
    doc = CaseDocument(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        doc_type="bill",
        source_label=filename,
        filename=filename,
        page_count=page_count,
        dedup_status="unique",
        status="extracted",
    )
    db.add(doc)
    db.flush()
    return doc


def _draft(db: Session, matter: Matter) -> DemandDraft:
    draft = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        strategy_plan_version=1,
        status="validated",
    )
    db.add(draft)
    db.flush()
    return draft


def _section(
    db: Session,
    matter: Matter,
    draft: DemandDraft,
    *,
    section_id: str,
    body: str,
    rendered: str | None = None,
) -> DraftSection:
    section = DraftSection(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        purpose="test",
        body_tokenized=body,
        rendered_preview=rendered,
        registry_version=draft.registry_version,
        validation="passed",
        sort_order=0,
    )
    db.add(section)
    db.flush()
    return section


def _kinds(findings) -> list[str]:
    return [f.check_kind for f in findings]


def _mint_amt_from_ledger(db: Session, matter: Matter, *, key: str) -> str:
    """Mint an AMT token from the matter's CURRENT ledger (real hash), return its bare id."""
    pack = load_pack(matter.jurisdiction)
    ledger = compute_matter_ledger(db, matter=matter, pack=pack)
    amt = next(a for a in _amounts(ledger) if a.key == key)
    registry.mint_amounts(db, matter=matter, amounts=[amt])
    db.refresh(matter)
    from sqlalchemy import select

    from app.models.orm import FactToken

    row = db.execute(
        select(FactToken).where(
            FactToken.matter_id == matter.id, FactToken.source_ref == f"amt:{key}"
        )
    ).scalar_one()
    return row.token_id


def _amounts(ledger):
    from app.money.specials import amounts_for_registry

    return amounts_for_registry(ledger)


def _billing_line(
    db: Session, matter: Matter, doc: CaseDocument, *, billed_cents: int
) -> BillingLine:
    line = BillingLine(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        provider="City ER",
        date_of_service=dt.date(2026, 1, 11),
        billed_cents=billed_cents,
        category="er",
        anchor={"document_id": str(doc.id), "page": 1},
    )
    db.add(line)
    db.flush()
    return line


# --------------------------------------------------------------------------------------
# orphan_token
# --------------------------------------------------------------------------------------


def test_orphan_token_flagged(db: Session, matter: Matter, user: User) -> None:
    draft = _draft(db, matter)
    _section(
        db, matter, draft, section_id="liability", body="Refers to [[FACT_999]] (never minted)."
    )
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    orphans = [f for f in findings if f.check_kind == CheckKind.ORPHAN_TOKEN.value]
    assert len(orphans) == 1
    assert orphans[0].section_id == "liability"
    assert "FACT_999" in orphans[0].detail


# --------------------------------------------------------------------------------------
# amt_ledger_mismatch — a billing edit after mint moves the live hash
# --------------------------------------------------------------------------------------


def test_amt_ledger_mismatch_after_billing_edit(db: Session, matter: Matter, user: User) -> None:
    doc = _document(db, matter, filename="bill.pdf", page_count=2)
    line = _billing_line(db, matter, doc, billed_cents=150000)
    db.commit()
    amt = _mint_amt_from_ledger(db, matter, key="specials.grand.billed")

    draft = _draft(db, matter)
    _section(
        db, matter, draft, section_id="damages_and_specials", body=f"Specials total [[{amt}]]."
    )

    # A billing edit AFTER the mint moves the live ledger hash away from the token's stored hash.
    line.billed_cents = 175000
    db.add(line)
    db.commit()

    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    mismatches = [f for f in findings if f.check_kind == CheckKind.AMT_LEDGER_MISMATCH.value]
    assert len(mismatches) == 1
    assert mismatches[0].section_id == "damages_and_specials"


def test_amt_matching_live_ledger_clean(db: Session, matter: Matter, user: User) -> None:
    doc = _document(db, matter, filename="bill.pdf", page_count=2)
    _billing_line(db, matter, doc, billed_cents=150000)
    db.commit()
    amt = _mint_amt_from_ledger(db, matter, key="specials.grand.billed")

    draft = _draft(db, matter)
    _section(
        db, matter, draft, section_id="damages_and_specials", body=f"Specials total [[{amt}]]."
    )
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    # No AMT mismatch: the live ledger still matches the minted hash.
    assert CheckKind.AMT_LEDGER_MISMATCH.value not in _kinds(findings)


# --------------------------------------------------------------------------------------
# dead_anchor — page_count shrink AND dedup supersession
# --------------------------------------------------------------------------------------


def test_dead_anchor_via_page_count_shrink(db: Session, matter: Matter, user: User) -> None:
    doc = _document(db, matter, filename="records.pdf", page_count=10)
    db.commit()
    anchor = PageAnchor(document_id=doc.id, page=8).model_dump(mode="json")
    fact = _mint_fact(db, matter, user, "the eighth-page finding", anchors=[anchor])
    draft = _draft(db, matter)
    _section(db, matter, draft, section_id="injuries", body=f"See [[{fact}]] in the record.")

    # Shrink the document so the anchor page (8) is now out of range.
    doc.page_count = 3
    db.add(doc)
    db.commit()

    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    dead = [f for f in findings if f.check_kind == CheckKind.DEAD_ANCHOR.value]
    assert len(dead) == 1
    assert dead[0].section_id == "injuries"
    # The offending anchor is carried onto the finding (compliance inv 11).
    assert dead[0].anchors and dead[0].anchors[0]["page"] == 8


def test_dead_anchor_via_dedup_supersession(db: Session, matter: Matter, user: User) -> None:
    doc = _document(db, matter, filename="dupe.pdf", page_count=5)
    db.commit()
    anchor = PageAnchor(document_id=doc.id, page=2).model_dump(mode="json")
    fact = _mint_fact(db, matter, user, "a fact anchored to the dupe", anchors=[anchor])
    draft = _draft(db, matter)
    _section(db, matter, draft, section_id="liability", body=f"Per [[{fact}]].")

    # The anchor's document is dedup-superseded (dropped out of the case).
    db.add(
        DedupDecision(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            document_id=doc.id,
            status="duplicate_of",
            page_hash_matches=[],
            resolution="superseded",
        )
    )
    db.commit()

    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    dead = [f for f in findings if f.check_kind == CheckKind.DEAD_ANCHOR.value]
    assert len(dead) == 1
    assert dead[0].anchors and str(dead[0].anchors[0]["document_id"]) == str(doc.id)


# --------------------------------------------------------------------------------------
# missing_exhibit — an EX token minted but not present in the manifest
# --------------------------------------------------------------------------------------


def test_missing_exhibit_when_not_in_manifest(db: Session, matter: Matter, user: User) -> None:
    doc = _document(db, matter, filename="exhibit-source.pdf", page_count=4)
    db.commit()
    # Mint an EX token directly for the document, but DO NOT create an Exhibit row — so the binder
    # manifest has no entry for it, and the check must flag the citation.
    registry.mint_exhibits(
        db,
        matter=matter,
        entries=[
            {
                "key": str(doc.id),
                "display_form": "Exhibit 1 — exhibit-source.pdf",
                "anchors": [{"document_id": str(doc.id), "page": 1}],
            }
        ],
    )
    db.refresh(matter)
    from sqlalchemy import select

    from app.models.orm import FactToken

    ex = db.execute(
        select(FactToken).where(
            FactToken.matter_id == matter.id, FactToken.source_ref == f"exhibit:{doc.id}"
        )
    ).scalar_one()

    draft = _draft(db, matter)
    _section(db, matter, draft, section_id="exhibits", body=f"See [[{ex.token_id}]].")
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    missing = [f for f in findings if f.check_kind == CheckKind.MISSING_EXHIBIT.value]
    assert len(missing) == 1
    assert missing[0].section_id == "exhibits"


def test_present_exhibit_clean(db: Session, matter: Matter, user: User) -> None:
    doc = _document(db, matter, filename="ok-exhibit.pdf", page_count=4)
    db.add(
        Exhibit(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            document_id=doc.id,
            include_pages=[1, 2],
            excluded_pages=[],
            phi_disposition="cleared",
            sort_order=0,
        )
    )
    db.commit()
    # Mint the EX token through the manifest (the sanctioned path) so it is integrity-ok + present.
    from app.package.manifest import build_draft_manifest

    manifest = build_draft_manifest(db, matter=matter, mint_tokens=True)
    db.refresh(matter)
    ex_token = next(e.exhibit_token for e in manifest.entries if e.document_id == doc.id)
    bare = ex_token[2:-2]

    draft = _draft(db, matter)
    _section(db, matter, draft, section_id="exhibits", body=f"See [[{bare}]].")
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    assert CheckKind.MISSING_EXHIBIT.value not in _kinds(findings)


# --------------------------------------------------------------------------------------
# prose_total_mismatch — a literal dollar figure matching no AMT display form
# --------------------------------------------------------------------------------------


def test_prose_total_mismatch_flags_unanchored_literal(
    db: Session, matter: Matter, user: User
) -> None:
    draft = _draft(db, matter)
    _section(
        db,
        matter,
        draft,
        section_id="damages_and_specials",
        body="Specials are substantial.",
        rendered="Total damages come to $9,999.99 in this matter.",
    )
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    prose = [f for f in findings if f.check_kind == CheckKind.PROSE_TOTAL_MISMATCH.value]
    assert len(prose) == 1
    # The span covers the literal in the rendered text.
    span = prose[0].span
    rendered = "Total damages come to $9,999.99 in this matter."
    assert rendered[span["start"] : span["end"]] == "$9,999.99"


def test_prose_total_matching_amt_display_is_clean(db: Session, matter: Matter, user: User) -> None:
    doc = _document(db, matter, filename="bill.pdf", page_count=2)
    _billing_line(db, matter, doc, billed_cents=150000)
    db.commit()
    amt = _mint_amt_from_ledger(db, matter, key="specials.grand.billed")
    from sqlalchemy import select

    from app.models.orm import FactToken

    display = db.execute(
        select(FactToken.display_form).where(
            FactToken.matter_id == matter.id, FactToken.token_id == amt
        )
    ).scalar_one()

    draft = _draft(db, matter)
    # The rendered literal EQUALS the AMT's display form -> not a mismatch.
    _section(
        db,
        matter,
        draft,
        section_id="damages_and_specials",
        body=f"Specials total [[{amt}]].",
        rendered=f"Specials total {display}.",
    )
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    assert CheckKind.PROSE_TOTAL_MISMATCH.value not in _kinds(findings)


# --------------------------------------------------------------------------------------
# undisposed_adverse — one finding when any adverse flag is undispositioned
# --------------------------------------------------------------------------------------


def test_undisposed_adverse_flagged(db: Session, matter: Matter, user: User) -> None:
    db.add(
        RiskFlag(
            id=uuid.uuid4(),
            firm_id=matter.firm_id,
            matter_id=matter.id,
            kind="preexisting_condition",
            severity="high",
            detector="label",
            anchors=[],
            detail="prior neck injury",
            disposition=None,
        )
    )
    db.commit()
    draft = _draft(db, matter)
    _section(db, matter, draft, section_id="liability", body="Fault is clear.")
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    adverse = [f for f in findings if f.check_kind == CheckKind.UNDISPOSED_ADVERSE.value]
    assert len(adverse) == 1
    # A draft-level finding carries the empty section id.
    assert adverse[0].section_id == ""
    assert "1 adverse" in adverse[0].detail


# --------------------------------------------------------------------------------------
# Clean draft — zero findings
# --------------------------------------------------------------------------------------


def test_clean_draft_zero_findings(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the incident")
    draft = _draft(db, matter)
    _section(
        db,
        matter,
        draft,
        section_id="liability",
        body=f"The claim arises from [[{fact}]].",
        rendered="The claim arises from the incident.",
    )
    ctx = build_check_context(db, matter=matter, draft=draft)
    findings = run_deterministic_checks(db, ctx)
    assert findings == []
