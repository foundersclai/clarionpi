"""G3 corrections tests (M5 Wave C) — span-patch, regen, and the mandatory re-verify.

Self-contained in-memory engine + firm/user/matter at ``compliance_review``. Tokens are minted via
the real registry; a plan/draft/section are seeded and a section is rendered so a patch has spans to
work with. Synthetic data only — no PHI.

Coverage: an AMT span-patch happy path (billing edited -> mismatch -> upstream re-mint -> patch
re-renders the new display form -> re-verify flips the finding RE_VERIFIED); a patch whose
re-rendered section fails validation ESCALATES the finding to the semantic bucket (regen); a regen
replaces the section content in place + marks the finding REGENERATED; and re-verify creating a NEW
open finding for a still-reproducing condition a prior fix did not cover.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.engine.compliance import corrections
from app.engine.compliance.corrections import apply_span_patch, re_verify, request_section_regen
from app.engine.tokenizer import registry
from app.models.enums import (
    CheckKind,
    FindingBucket,
    FindingStatus,
    GateState,
)
from app.models.orm import (
    BillingLine,
    CaseDocument,
    ComplianceFinding,
    DemandDraft,
    DraftSection,
    Firm,
    Matter,
    StrategyPlan,
    User,
)
from app.money.assemble import compute_matter_ledger
from app.money.specials import amounts_for_registry
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


def _mint_fact(db: Session, matter: Matter, user: User, display: str) -> str:
    row = registry.mint_attorney_fact(
        db, matter=matter, user=user, display_form=display, value={"note": display}
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


def _mint_amt(db: Session, matter: Matter, *, key: str) -> str:
    pack = load_pack(matter.jurisdiction)
    ledger = compute_matter_ledger(db, matter=matter, pack=pack)
    amt = next(a for a in amounts_for_registry(ledger) if a.key == key)
    registry.mint_amounts(db, matter=matter, amounts=[amt])
    db.refresh(matter)
    from app.models.orm import FactToken

    rows = list(
        db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id, FactToken.source_ref == f"amt:{key}"
            )
        ).scalars()
    )
    # A re-mint supersedes the slot (a new version row); take the latest-version token id.
    return max(rows, key=lambda r: r.registry_version).token_id


def _plan(db: Session, matter: Matter, sections: list) -> StrategyPlan:
    plan = StrategyPlan(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        demand_amount_cents=None,
        demand_type="open",
        sections=[s.model_dump() for s in sections],
        emphasis_directives=[],
        approved=True,
    )
    db.add(plan)
    db.flush()
    return plan


def _draft(db: Session, matter: Matter, plan: StrategyPlan) -> DemandDraft:
    draft = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=plan.registry_version,
        strategy_plan_version=plan.version,
        status="in_compliance",
    )
    db.add(draft)
    db.flush()
    return draft


def _section(
    db: Session, matter: Matter, draft: DemandDraft, *, section_id: str, body: str
) -> DraftSection:
    from app.engine.brain2.renderer import render_section

    section = DraftSection(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        purpose="test",
        body_tokenized=body,
        registry_version=draft.registry_version,
        validation="passed",
        sort_order=0,
    )
    db.add(section)
    db.flush()
    render_section(db, matter=matter, section=section)
    db.flush()
    return section


def _finding(
    db: Session,
    matter: Matter,
    draft: DemandDraft,
    *,
    section_id: str,
    check_kind: CheckKind,
    bucket: FindingBucket,
    status: FindingStatus = FindingStatus.OPEN,
    span: dict | None = None,
    detail: str = "planted",
) -> ComplianceFinding:
    finding = ComplianceFinding(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        registry_version=draft.registry_version,
        check_kind=check_kind.value,
        bucket=bucket.value,
        severity="blocking",
        detail=detail,
        anchors=[],
        span=span,
        status=status.value,
    )
    db.add(finding)
    db.flush()
    return finding


# --------------------------------------------------------------------------------------
# AMT span-patch happy path — re-mint upstream, patch re-renders, re-verify flips RE_VERIFIED
# --------------------------------------------------------------------------------------


def test_amt_span_patch_happy_then_re_verified(db: Session, matter: Matter, user: User) -> None:
    from app.models.schemas import PlannedSection

    doc = _document(db, matter, filename="bill.pdf", page_count=2)
    line = _billing_line(db, matter, doc, billed_cents=150000)
    db.commit()
    amt = _mint_amt(db, matter, key="specials.grand.billed")

    planned = PlannedSection(
        section_id="damages_and_specials",
        purpose="State specials.",
        allowed_tokens=[amt],
        required_tokens=[amt],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    section = _section(
        db, matter, draft, section_id="damages_and_specials", body=f"Specials total [[{amt}]]."
    )
    old_rendered = section.rendered_preview

    # Billing edited -> the live ledger hash moves -> an amt_mismatch would be found.
    line.billed_cents = 175000
    db.add(line)
    db.commit()

    finding = _finding(
        db,
        matter,
        draft,
        section_id="damages_and_specials",
        check_kind=CheckKind.AMT_LEDGER_MISMATCH,
        bucket=FindingBucket.MECHANICAL,
        span=_span_for(section, amt),
    )

    # Upstream fix: re-sync the ledger so the AMT token carries the NEW hash + display form.
    amt2 = _mint_amt(db, matter, key="specials.grand.billed")
    assert amt2 == amt  # same slot, superseded

    patched = apply_span_patch(db, matter=matter, draft=draft, finding=finding)
    assert patched.status == FindingStatus.PATCHED.value
    db.refresh(section)
    # The re-render picked up the new display form ($1,750.00), not the stale one.
    assert section.rendered_preview != old_rendered
    assert "$1,750.00" in section.rendered_preview

    # Re-verify: the mismatch no longer reproduces -> the finding flips RE_VERIFIED.
    touched = re_verify(db, None, matter=matter, plan=plan, draft=draft)
    db.refresh(finding)
    assert finding.status == FindingStatus.RE_VERIFIED.value
    assert finding in touched


def _span_for(section: DraftSection, bare_token_id: str) -> dict | None:
    for span in section.spans:
        if span.get("token_id") == bare_token_id:
            return {"start": span["start"], "end": span["end"]}
    return None


# --------------------------------------------------------------------------------------
# Patch-validation failure escalates the finding to the semantic bucket (regen)
# --------------------------------------------------------------------------------------


def test_patch_validation_failure_escalates_to_semantic(
    db: Session, matter: Matter, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.schemas import PlannedSection

    fact = _mint_fact(db, matter, user, "the incident")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    _section(db, matter, draft, section_id="liability", body=f"Fault from [[{fact}]].")
    finding = _finding(
        db,
        matter,
        draft,
        section_id="liability",
        check_kind=CheckKind.PROSE_TOTAL_MISMATCH,
        bucket=FindingBucket.MECHANICAL,
    )

    # Force the post-render validation to fail -> the mechanical splice would land an invalid
    # section, so the patch must ESCALATE to the semantic bucket rather than ship the splice.
    monkeypatch.setattr(
        corrections, "validate_section", lambda *a, **k: ["forced validation failure"]
    )
    result = apply_span_patch(db, matter=matter, draft=draft, finding=finding)
    assert result.bucket == FindingBucket.SEMANTIC.value
    assert result.status == FindingStatus.OPEN.value  # NOT patched
    assert "span-patch failed validation -> regen" in result.detail


# --------------------------------------------------------------------------------------
# Regen replaces the section content in place + marks the finding REGENERATED
# --------------------------------------------------------------------------------------


def test_regen_replaces_content_in_place(db: Session, matter: Matter, user: User) -> None:
    from app.models.schemas import PlannedSection

    fact = _mint_fact(db, matter, user, "the incident")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact],
        required_tokens=[fact],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    section = _section(db, matter, draft, section_id="liability", body=f"Old text [[{fact}]].")
    original_id = section.id
    finding = _finding(
        db,
        matter,
        draft,
        section_id="liability",
        check_kind=CheckKind.STRATEGY_DRIFT,
        bucket=FindingBucket.SEMANTIC,
    )

    provider = ScriptedProvider(
        [
            CompletionResult(
                text=json.dumps({"body_tokenized": f"Regenerated text with [[{fact}]]."}),
                input_tokens=40,
                output_tokens=20,
                cost_cents=1,
            )
        ]
    )
    client = MeteredLLMClient(provider, db, matter.firm_id, matter.id)
    new_section, updated = request_section_regen(
        db, client, matter=matter, plan=plan, draft=draft, finding=finding
    )
    # Same row id — the content was replaced IN PLACE (not a new row).
    assert new_section.id == original_id
    assert "Regenerated text" in new_section.body_tokenized
    assert updated.status == FindingStatus.REGENERATED.value
    # Exactly one DraftSection row for the section (the extra regen row was folded + dropped).
    rows = list(
        db.execute(
            select(DraftSection).where(
                DraftSection.draft_id == draft.id, DraftSection.section_id == "liability"
            )
        ).scalars()
    )
    assert len(rows) == 1
    # Re-render happened: the fresh display form is in the rendered preview.
    assert new_section.rendered_preview == "Regenerated text with the incident."


# --------------------------------------------------------------------------------------
# Re-verify creates a NEW open finding for a still-reproducing condition
# --------------------------------------------------------------------------------------


def test_re_verify_creates_new_finding_for_reproducing_condition(
    db: Session, matter: Matter, user: User
) -> None:
    from app.models.schemas import PlannedSection

    fact = _mint_fact(db, matter, user, "the incident")
    planned = PlannedSection(
        section_id="liability",
        purpose="Establish fault.",
        allowed_tokens=[fact, "FACT_404"],
        required_tokens=[],
        max_words=100,
    )
    plan = _plan(db, matter, [planned])
    draft = _draft(db, matter, plan)
    # The section cites an ORPHAN token ([[FACT_404]] never minted) alongside a valid one.
    _section(db, matter, draft, section_id="liability", body=f"Per [[{fact}]] and [[FACT_404]].")

    # A prior fix: a PATCHED finding whose condition no longer reproduces (no matching check).
    patched = _finding(
        db,
        matter,
        draft,
        section_id="liability",
        check_kind=CheckKind.PROSE_TOTAL_MISMATCH,
        bucket=FindingBucket.MECHANICAL,
        status=FindingStatus.PATCHED,
    )

    touched = re_verify(db, None, matter=matter, plan=plan, draft=draft)
    db.refresh(patched)
    # The patched finding flipped RE_VERIFIED (its condition is gone).
    assert patched.status == FindingStatus.RE_VERIFIED.value
    # A NEW OPEN orphan finding was created for the still-reproducing orphan.
    orphans = list(
        db.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.draft_id == draft.id,
                ComplianceFinding.check_kind == CheckKind.ORPHAN_TOKEN.value,
                ComplianceFinding.status == FindingStatus.OPEN.value,
            )
        ).scalars()
    )
    assert len(orphans) == 1
    assert orphans[0] in touched
