"""Drafting/compliance API tests (M5 Wave D1) — plan emit + G2.5 edit/approve, demand generate,
finding actions, and the G3-approve integration.

Mirrors ``test_analysis_api.py``: the conftest ``client`` + per-test ``seeded`` users and an
in-test ``AUTH_MODE=session`` monkeypatch. Matters are created through the real API (so the AZ pack
is on them), parked at a gate by direct ORM state set, and tokens / plans / drafts / sections /
findings are seeded via the real registry + direct ORM. A
:class:`~app.core.llm_provider.ScriptedProvider` drives the LLM-backed routes (plan emit emphasis,
the demand run's memo + sections + judge); ``NullProvider`` is used where the model is expected to
degrade. Synthetic data only — no PHI.

Coverage:
- plan emit: fence (409 off ``plan_review``) + happy (a plan row, ``approved=False``, plan view);
- G2.5 edit: creates plan version+1 unapproved (top-level + per-section overrides) and an
  unknown-section 422;
- G2.5 approve (the side effect): stamps ``approved``/``approved_by``/``approved_at``; refuses with
  ``plan_missing`` when no plan; refuses with ``plan_registry_drift`` after a registry bump;
- demand generate: fence + a full scripted stream (section frames + gate_ready, gate at
  ``compliance_review``, compliance pass HAVING RUN — findings/draft present);
- finding actions: accept-without-reason 422 (schema), non-attorney 403, hard-block 409, patch
  happy (AMT mismatch planted -> re-verify -> open_blocking drops), regen happy (REGENERATED);
- THE INTEGRATION MOMENT: G3 approve via the gates API refused with an open blocking finding
  (guard_failed no_blocking_findings), then passes after the finding is dispositioned.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    DEV_FIRM_ID,
    DEV_PARALEGAL_EMAIL,
    DEV_USER_EMAIL,
    DEV_USER_PASSWORD,
    seed_dev_users,
)
from app.api.routes.ingest import get_provider
from app.core.config import get_settings
from app.core.llm_provider import CompletionResult, NullProvider, ScriptedProvider
from app.engine.tokenizer import registry
from app.main import app
from app.models.enums import (
    CheckKind,
    DraftStatus,
    FindingBucket,
    FindingStatus,
    GateAction,
    GateState,
    LedgerCategory,
)
from app.models.orm import (
    BillingLine,
    CaseDocument,
    ComplianceFinding,
    DemandDraft,
    DraftSection,
    Matter,
    StrategyPlan,
)
from app.models.schemas import PlannedSection

# --------------------------------------------------------------------------------------
# Auth + provider fixtures (mirror test_analysis_api.py)
# --------------------------------------------------------------------------------------


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    db = session_factory()
    try:
        seed_dev_users(db)
    finally:
        db.close()
    return session_factory


@pytest.fixture
def session_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTH_MODE", "session")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _use_provider(provider: object) -> None:
    """Override the run/route provider dependency with ``provider`` (cleared by teardown)."""
    app.dependency_overrides[get_provider] = lambda: provider


@pytest.fixture(autouse=True)
def _clear_provider_override() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_provider, None)


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": DEV_USER_PASSWORD})
    assert resp.status_code == 200, resp.text


def _create_matter(client: TestClient) -> uuid.UUID:
    resp = client.post(
        "/api/matters",
        json={
            "client_display_name": "Drafting API Client",
            "claim_type": "mva",
            "incident_date": "2026-01-15",
            "jurisdiction": "AZ",
            # WI-2: the four intake flags are REQUIRED; all-"no" is the in-box matter.
            "public_entity_involved": "no",
            "plaintiff_is_minor": "no",
            "wrongful_death": "no",
            "coverage_dispute": "no",
        },
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


def _park(session_factory: sessionmaker[Session], matter_id: uuid.UUID, state: GateState) -> None:
    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        matter.gate_state = state.value
        db.commit()
    finally:
        db.close()


def _freeze_registry(session_factory: sessionmaker[Session], matter_id: uuid.UUID) -> None:
    """Freeze a RegistryVersion at the matter's current version (the G2a freeze the guard reads).

    ``registry_version_match`` (on the G2.5 + G3 approve edges) requires a FROZEN RegistryVersion
    pinned to the matter's current version. The G2a side effect creates it in the real flow; these
    tests seed a compliance/plan draft directly, so this stands in for that freeze.
    """
    from app.models.orm import RegistryVersion

    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        # The registry sync may already have minted the row for this version — freeze it in place
        # (a fresh INSERT would collide with the (matter_id, version) unique constraint).
        row = db.execute(
            select(RegistryVersion).where(
                RegistryVersion.matter_id == matter.id,
                RegistryVersion.version == matter.registry_version,
            )
        ).scalar_one_or_none()
        if row is None:
            row = RegistryVersion(
                firm_id=DEV_FIRM_ID,
                matter_id=matter.id,
                version=matter.registry_version,
                frozen=True,
                parent_version=None,
                change_reason="test_freeze",
            )
            db.add(row)
        else:
            row.frozen = True
        db.commit()
    finally:
        db.close()


def _payload_version(session_factory: sessionmaker[Session], matter_id: uuid.UUID) -> int:
    """The current optimistic-fence token (registry_version + gate-record count) for a submit."""
    from app.engine.orchestrator.service import payload_version

    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        return payload_version(db, matter=matter)
    finally:
        db.close()


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=50, output_tokens=30, cost_cents=1)


def _emphasis(*directives: str) -> CompletionResult:
    return _completion(json.dumps({"emphasis_directives": list(directives)}))


def _memo(body: str = "The strategy is straightforward.") -> CompletionResult:
    return _completion(json.dumps({"memo": body}))


def _section(body: str) -> CompletionResult:
    return _completion(json.dumps({"body_tokenized": body}))


def _judge_clean() -> CompletionResult:
    """A judge reply with NO semantic findings (the deterministic pass still stands)."""
    return _completion(json.dumps({"findings": []}))


def _sse_events(resp_text: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for frame in resp_text.split("\n\n"):
        frame = frame.strip()
        if not frame.startswith("event: "):
            continue
        lines = frame.split("\n")
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        parsed.append((event, data))
    return parsed


# --------------------------------------------------------------------------------------
# Seed helpers (direct ORM + real registry)
# --------------------------------------------------------------------------------------


def _mint_fact(db: Session, matter: Matter, user_id: uuid.UUID, display: str) -> str:
    from app.models.orm import User

    user = db.get(User, user_id)
    row = registry.mint_attorney_fact(
        db, matter=matter, user=user, display_form=display, value={"note": display}
    )
    db.refresh(matter)
    return row.token_id


def _dev_attorney_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    from app.api.deps import DEV_USER_ID

    return DEV_USER_ID


def _two_section_plan(
    session_factory: sessionmaker[Session], matter_id: uuid.UUID, *, approved: bool
) -> tuple[int, str]:
    """Seed an (un)approved 2-section plan: liability (requires a FACT) + intro (no tokens).

    Returns ``(plan_version, fact_token_id)``. Minted at the matter's current registry version.
    """
    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        fact = _mint_fact(db, matter, _dev_attorney_id(session_factory), "the initial visit")
        liability = PlannedSection(
            section_id="liability",
            purpose="Establish fault.",
            allowed_tokens=[fact],
            required_tokens=[fact],
            max_words=100,
        )
        intro = PlannedSection(
            section_id="intro_and_representation",
            purpose="Introduce representation.",
            allowed_tokens=[],
            required_tokens=[],
            max_words=100,
        )
        plan = StrategyPlan(
            firm_id=DEV_FIRM_ID,
            matter_id=matter.id,
            version=1,
            registry_version=matter.registry_version,
            demand_amount_cents=None,
            demand_type="open",
            sections=[s.model_dump() for s in (liability, intro)],
            emphasis_directives=[],
            approved=approved,
        )
        db.add(plan)
        db.commit()
        return plan.version, fact
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# plan emit — fence + happy
# --------------------------------------------------------------------------------------


def test_plan_emit_fence_off_plan_review(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)  # not plan_review
    _use_provider(NullProvider())

    resp = client.post(f"/api/matters/{matter_id}/plan/emit")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "gate_state_mismatch"


def test_plan_emit_happy_returns_unapproved_plan(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.PLAN_REVIEW)
    # A live provider so the emphasis pass runs (one bounded call).
    _use_provider(ScriptedProvider([_emphasis("Foreground the rear-end liability.")]))

    resp = client.post(f"/api/matters/{matter_id}/plan/emit")
    assert resp.status_code == 200, resp.text
    plan = resp.json()["plan"]
    assert plan["approved"] is False
    assert plan["demand_type"] == "open"
    # The AZ five-section skeleton is present, deterministic.
    section_ids = [s["section_id"] for s in plan["sections"]]
    assert section_ids == [
        "intro_and_representation",
        "liability",
        "injuries_and_treatment",
        "damages_and_specials",
        "demand_and_deadline",
    ]
    assert plan["emphasis_directives"] == ["Foreground the rear-end liability."]

    # A StrategyPlan row was written.
    db = seeded()
    try:
        rows = list(
            db.execute(select(StrategyPlan).where(StrategyPlan.matter_id == matter_id)).scalars()
        )
        assert len(rows) == 1 and rows[0].approved is False
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# G2.5 edit — creates a new unapproved version; unknown section 422
# --------------------------------------------------------------------------------------


def _submit(
    client: TestClient,
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    action: GateAction,
    edits: dict | None = None,
    override_reason: str | None = None,
) -> object:
    body = {
        "action": action.value,
        "idempotency_key": uuid.uuid4().hex,
        "payload_version": _payload_version(session_factory, matter_id),
    }
    if edits is not None:
        body["edits"] = edits
    if override_reason is not None:
        body["override_reason"] = override_reason
    return client.post(
        f"/api/matters/{matter_id}/gates/{GateState.PLAN_REVIEW.value}/submit", json=body
    )


def test_g25_edit_creates_new_unapproved_version(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _two_section_plan(seeded, matter_id, approved=False)  # v1
    _park(seeded, matter_id, GateState.PLAN_REVIEW)

    resp = _submit(
        client,
        seeded,
        matter_id,
        action=GateAction.EDIT,
        edits={
            "demand_amount_cents": 5000000,
            "emphasis_directives": ["Lead with the surgery."],
            "sections": [{"section_id": "liability", "max_words": 250}],
        },
    )
    assert resp.status_code == 200, resp.text

    db = seeded()
    try:
        plans = sorted(
            db.execute(select(StrategyPlan).where(StrategyPlan.matter_id == matter_id)).scalars(),
            key=lambda p: p.version,
        )
        assert [p.version for p in plans] == [1, 2]
        v2 = plans[1]
        assert v2.approved is False
        assert v2.demand_amount_cents == 5000000
        assert v2.emphasis_directives == ["Lead with the surgery."]
        liability = next(s for s in v2.sections if s["section_id"] == "liability")
        assert liability["max_words"] == 250
        # The intro section carried over unchanged.
        assert any(s["section_id"] == "intro_and_representation" for s in v2.sections)
    finally:
        db.close()


def test_g25_edit_unknown_section_422(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _two_section_plan(seeded, matter_id, approved=False)
    _park(seeded, matter_id, GateState.PLAN_REVIEW)

    resp = _submit(
        client,
        seeded,
        matter_id,
        action=GateAction.EDIT,
        edits={"sections": [{"section_id": "no_such_section", "max_words": 100}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "unknown_plan_section"
    assert resp.json()["section_id"] == "no_such_section"
    # No new version was created (the edit refused whole).
    db = seeded()
    try:
        plans = list(
            db.execute(select(StrategyPlan).where(StrategyPlan.matter_id == matter_id)).scalars()
        )
        assert len(plans) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# G2.5 approve — the side effect stamps the plan; plan_missing / plan_registry_drift refusals
# --------------------------------------------------------------------------------------


def test_g25_approve_stamps_plan(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _two_section_plan(seeded, matter_id, approved=False)
    _freeze_registry(seeded, matter_id)  # registry_version_match needs a frozen pin (G2a in flow)
    _park(seeded, matter_id, GateState.PLAN_REVIEW)

    resp = _submit(client, seeded, matter_id, action=GateAction.APPROVE)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["to_state"] == GateState.DRAFTING.value

    db = seeded()
    try:
        plan = db.execute(
            select(StrategyPlan).where(StrategyPlan.matter_id == matter_id)
        ).scalar_one()
        assert plan.approved is True
        assert plan.approved_by == _dev_attorney_id(seeded)
        assert plan.approved_at is not None
    finally:
        db.close()


def test_g25_approve_plan_missing_refusal(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    # Freeze so registry_version_match passes; then only the side effect's plan_missing can refuse.
    _freeze_registry(seeded, matter_id)
    _park(seeded, matter_id, GateState.PLAN_REVIEW)  # NO plan emitted

    resp = _submit(client, seeded, matter_id, action=GateAction.APPROVE)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "guard_failed"
    assert body["guard"] == "strategy_plan"
    assert body["code"] == "plan_missing"
    # State unchanged (still plan_review).
    db = seeded()
    try:
        assert db.get(Matter, matter_id).gate_state == GateState.PLAN_REVIEW.value
    finally:
        db.close()


def test_g25_approve_plan_registry_drift_refusal(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _two_section_plan(seeded, matter_id, approved=False)  # plan pinned at registry_version 0

    # Bump the matter's registry version past the plan's AND freeze the NEW version, so the
    # matter-level ``registry_version_match`` guard PASSES (pinned == current) and only the
    # plan-level bind (this side effect's ``plan_registry_drift``) catches the stale plan.
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        matter.registry_version = matter.registry_version + 1
        db.commit()
    finally:
        db.close()
    _freeze_registry(seeded, matter_id)  # pins the NEW version
    _park(seeded, matter_id, GateState.PLAN_REVIEW)

    resp = _submit(client, seeded, matter_id, action=GateAction.APPROVE)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "guard_failed"
    assert body["code"] == "plan_registry_drift"


# --------------------------------------------------------------------------------------
# demand generate — fence + full scripted stream (compliance pass ran)
# --------------------------------------------------------------------------------------


def test_demand_generate_fence_off_drafting(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.PLAN_REVIEW)  # not drafting
    _use_provider(NullProvider())

    resp = client.post(f"/api/matters/{matter_id}/demand/generate")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "gate_state_mismatch"


def test_demand_generate_full_stream_runs_compliance(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _, fact = _two_section_plan(seeded, matter_id, approved=True)
    _park(seeded, matter_id, GateState.DRAFTING)

    # memo + 2 sections + judge (2 clean judge calls, one per section — the post_draft compliance
    # pass runs the semantic judge over each PASSED section).
    _use_provider(
        ScriptedProvider(
            [
                _memo(),
                _section(f"Fault is clear from [[{fact}]]."),  # liability — passes
                _section("We represent the claimant and present this demand."),  # intro — passes
                _judge_clean(),
                _judge_clean(),
            ]
        )
    )

    resp = client.post(f"/api/matters/{matter_id}/demand/generate")
    assert resp.status_code == 200, resp.text
    events = _sse_events(resp.text)
    names = [n for n, _ in events]
    assert "section" in names
    assert "gate_ready" in names
    gate_ready = [d for n, d in events if n == "gate_ready"][0]
    assert gate_ready["gate"] == "compliance_review"

    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        assert matter.gate_state == GateState.COMPLIANCE_REVIEW.value
        draft = db.execute(
            select(DemandDraft).where(DemandDraft.matter_id == matter_id)
        ).scalar_one()
        # The compliance pass HAS RUN (post_draft): the draft advanced VALIDATED -> in_compliance.
        assert draft.status == DraftStatus.IN_COMPLIANCE.value
    finally:
        db.close()

    # The populated compliance_review VM is fetchable + wire-safe (rendered previews, never tokens).
    env = client.get(f"/api/matters/{matter_id}/gates/current")
    assert env.status_code == 200, env.text
    vm = env.json()["view_model"]
    assert vm["draft"]["status"] == DraftStatus.IN_COMPLIANCE.value
    assert [s["section_id"] for s in vm["sections"]] == ["liability", "intro_and_representation"]
    assert all("[[" not in (s["rendered_preview"] or "") for s in vm["sections"])
    assert "buckets" in vm and "open_blocking" in vm


# --------------------------------------------------------------------------------------
# Compliance seed helpers (a draft parked at compliance_review with a planted finding)
# --------------------------------------------------------------------------------------


def _seed_compliance_draft(
    session_factory: sessionmaker[Session], matter_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    """Seed an approved plan + a rendered ``damages_and_specials`` draft with an AMT token.

    Returns ``(draft_id, amt_token_id)``. The matter is left at ``compliance_review``.
    """
    from app.engine.brain2.renderer import render_section
    from app.models.orm import FactToken
    from app.money.assemble import compute_matter_ledger
    from app.money.specials import amounts_for_registry
    from app.rules.loader import load_pack

    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        doc = CaseDocument(
            firm_id=DEV_FIRM_ID,
            matter_id=matter.id,
            doc_type="bill",
            source_label="bill.pdf",
            filename="bill.pdf",
            page_count=2,
            dedup_status="unique",
            status="extracted",
        )
        db.add(doc)
        db.flush()
        db.add(
            BillingLine(
                firm_id=DEV_FIRM_ID,
                matter_id=matter.id,
                provider="City ER",
                date_of_service=dt.date(2026, 1, 11),
                billed_cents=150000,
                category=LedgerCategory.ER.value,
                anchor={"document_id": str(doc.id), "page": 1},
            )
        )
        db.commit()

        pack = load_pack(matter.jurisdiction)
        ledger = compute_matter_ledger(db, matter=matter, pack=pack)
        amt = next(a for a in amounts_for_registry(ledger) if a.key == "specials.grand.billed")
        registry.mint_amounts(db, matter=matter, amounts=[amt])
        db.refresh(matter)
        amt_token = max(
            db.execute(
                select(FactToken).where(
                    FactToken.matter_id == matter.id,
                    FactToken.source_ref == "amt:specials.grand.billed",
                )
            ).scalars(),
            key=lambda r: r.registry_version,
        ).token_id

        planned = PlannedSection(
            section_id="damages_and_specials",
            purpose="State specials.",
            allowed_tokens=[amt_token],
            required_tokens=[amt_token],
            max_words=100,
        )
        plan = StrategyPlan(
            firm_id=DEV_FIRM_ID,
            matter_id=matter.id,
            version=1,
            registry_version=matter.registry_version,
            demand_amount_cents=None,
            demand_type="open",
            sections=[planned.model_dump()],
            emphasis_directives=[],
            approved=True,
        )
        db.add(plan)
        db.flush()
        draft = DemandDraft(
            firm_id=DEV_FIRM_ID,
            matter_id=matter.id,
            version=1,
            registry_version=plan.registry_version,
            strategy_plan_version=plan.version,
            status=DraftStatus.IN_COMPLIANCE.value,
        )
        db.add(draft)
        db.flush()
        section = DraftSection(
            firm_id=DEV_FIRM_ID,
            draft_id=draft.id,
            section_id="damages_and_specials",
            purpose="State specials.",
            body_tokenized=f"Specials total [[{amt_token}]].",
            registry_version=draft.registry_version,
            validation="passed",
            sort_order=0,
        )
        db.add(section)
        db.flush()
        render_section(db, matter=matter, section=section)
        matter.gate_state = GateState.COMPLIANCE_REVIEW.value
        db.commit()
        return draft.id, amt_token
    finally:
        db.close()


def _plant_finding(
    session_factory: sessionmaker[Session],
    draft_id: uuid.UUID,
    *,
    check_kind: CheckKind,
    bucket: FindingBucket,
    section_id: str = "damages_and_specials",
    span_token: str | None = None,
) -> uuid.UUID:
    db = session_factory()
    try:
        draft = db.get(DemandDraft, draft_id)
        span = None
        if span_token is not None:
            section = db.execute(
                select(DraftSection).where(
                    DraftSection.draft_id == draft_id, DraftSection.section_id == section_id
                )
            ).scalar_one()
            for s in section.spans:
                if s.get("token_id") == span_token:
                    span = {"start": s["start"], "end": s["end"]}
        finding = ComplianceFinding(
            firm_id=DEV_FIRM_ID,
            draft_id=draft_id,
            section_id=section_id,
            registry_version=draft.registry_version,
            check_kind=check_kind.value,
            bucket=bucket.value,
            severity="blocking",
            detail="planted",
            anchors=[],
            span=span,
            status=FindingStatus.OPEN.value,
        )
        db.add(finding)
        db.commit()
        return finding.id
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# Finding actions — schema 422, role 403, hard block 409, patch happy, regen happy
# --------------------------------------------------------------------------------------


def test_finding_accept_without_reason_422(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    draft_id, _ = _seed_compliance_draft(seeded, matter_id)
    finding_id = _plant_finding(
        seeded, draft_id, check_kind=CheckKind.STRATEGY_DRIFT, bucket=FindingBucket.SEMANTIC
    )
    _use_provider(NullProvider())

    # accept with no override_reason -> the FindingActionRequest schema rejects it (422).
    resp = client.post(f"/api/findings/{finding_id}/action", json={"action": "accept"})
    assert resp.status_code == 422, resp.text


def test_finding_non_attorney_disposition_403(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    draft_id, _ = _seed_compliance_draft(seeded, matter_id)
    finding_id = _plant_finding(
        seeded, draft_id, check_kind=CheckKind.STRATEGY_DRIFT, bucket=FindingBucket.SEMANTIC
    )
    _use_provider(NullProvider())

    # Re-login as the paralegal: disposition is attorney-only.
    _login(client, DEV_PARALEGAL_EMAIL)
    resp = client.post(
        f"/api/findings/{finding_id}/action",
        json={"action": "accept", "override_reason": "acceptable as framed"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "role_forbidden"


def test_finding_hard_block_accept_409(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    draft_id, _ = _seed_compliance_draft(seeded, matter_id)
    # A hard block (orphan_token) can never be dispositioned to ship.
    finding_id = _plant_finding(
        seeded, draft_id, check_kind=CheckKind.ORPHAN_TOKEN, bucket=FindingBucket.SEMANTIC
    )
    _use_provider(NullProvider())

    resp = client.post(
        f"/api/findings/{finding_id}/action",
        json={"action": "override", "override_reason": "proceed anyway"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "hard_block_not_disposable"


def test_finding_patch_happy_drops_open_blocking(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    draft_id, amt = _seed_compliance_draft(seeded, matter_id)

    # Edit the billing line so the live ledger hash moves (an AMT mismatch reproduces), then
    # re-sync the ledger so the AMT token carries the NEW display form (the upstream fix a patch
    # re-renders). Plant a mechanical AMT-mismatch finding with the span.
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        line = db.execute(
            select(BillingLine).where(BillingLine.matter_id == matter_id)
        ).scalar_one()
        line.billed_cents = 175000
        db.commit()
        from app.money.assemble import compute_matter_ledger
        from app.money.specials import amounts_for_registry
        from app.rules.loader import load_pack

        pack = load_pack(matter.jurisdiction)
        ledger = compute_matter_ledger(db, matter=matter, pack=pack)
        amt_val = next(a for a in amounts_for_registry(ledger) if a.key == "specials.grand.billed")
        registry.mint_amounts(db, matter=matter, amounts=[amt_val])
        db.commit()
    finally:
        db.close()

    finding_id = _plant_finding(
        seeded,
        draft_id,
        check_kind=CheckKind.AMT_LEDGER_MISMATCH,
        bucket=FindingBucket.MECHANICAL,
        span_token=amt,
    )
    _use_provider(NullProvider())  # patch + re-verify are deterministic (no live model needed)

    resp = client.post(f"/api/findings/{finding_id}/action", json={"action": "patch"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The finding re-verified (mismatch no longer reproduces) -> open_blocking drops to 0.
    assert body["finding"]["status"] == FindingStatus.RE_VERIFIED.value
    assert body["open_blocking"] == 0


def test_finding_regen_happy(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    draft_id, amt = _seed_compliance_draft(seeded, matter_id)
    finding_id = _plant_finding(
        seeded, draft_id, check_kind=CheckKind.STRATEGY_DRIFT, bucket=FindingBucket.SEMANTIC
    )
    # regen re-drafts the section (one drafter call) then re-verify runs the judge (one call).
    _use_provider(
        ScriptedProvider([_section(f"Specials total [[{amt}]] as revised."), _judge_clean()])
    )

    resp = client.post(f"/api/findings/{finding_id}/action", json={"action": "regen"})
    assert resp.status_code == 200, resp.text
    # regen marks REGENERATED, then the mandatory re-verify flips it (the planted strategy_drift
    # does not reproduce against the clean judge) -> RE_VERIFIED; open_blocking clears.
    assert resp.json()["finding"]["status"] == FindingStatus.RE_VERIFIED.value
    assert resp.json()["open_blocking"] == 0


# --------------------------------------------------------------------------------------
# THE INTEGRATION MOMENT — G3 approve refused with an open blocker, then passes after disposition
# --------------------------------------------------------------------------------------


def test_g3_approve_blocked_then_passes_after_disposition(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    draft_id, _ = _seed_compliance_draft(seeded, matter_id)
    _freeze_registry(seeded, matter_id)  # G3 approve's registry_version_match needs a frozen pin
    # An open SEMANTIC blocker (dispositionable, unlike a hard block).
    _plant_finding(
        seeded, draft_id, check_kind=CheckKind.STRATEGY_DRIFT, bucket=FindingBucket.SEMANTIC
    )
    _use_provider(NullProvider())

    gate = GateState.COMPLIANCE_REVIEW.value

    # 1) G3 approve is refused: the no_blocking_findings guard fails (guard_failed).
    resp = client.post(
        f"/api/matters/{matter_id}/gates/{gate}/submit",
        json={
            "action": GateAction.APPROVE.value,
            "idempotency_key": uuid.uuid4().hex,
            "payload_version": _payload_version(seeded, matter_id),
        },
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "guard_failed"
    assert body["guard"] == "no_blocking_findings"

    # 2) Disposition the finding (attorney override with a reason).
    finding_id = _open_finding_id(seeded, draft_id)
    disp = client.post(
        f"/api/findings/{finding_id}/action",
        json={"action": "override", "override_reason": "acceptable given the record"},
    )
    assert disp.status_code == 200, disp.text
    assert disp.json()["open_blocking"] == 0

    # 3) G3 approve now passes -> matter advances to package_assembly.
    resp = client.post(
        f"/api/matters/{matter_id}/gates/{gate}/submit",
        json={
            "action": GateAction.APPROVE.value,
            "idempotency_key": uuid.uuid4().hex,
            "payload_version": _payload_version(seeded, matter_id),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["to_state"] == GateState.PACKAGE_ASSEMBLY.value
    # WD-2: the allowed G3 approve marks the current draft APPROVED (same wire flow, HTTP level).
    _db = seeded()
    try:
        assert _db.get(DemandDraft, draft_id).status == DraftStatus.APPROVED.value
    finally:
        _db.close()


def _open_finding_id(session_factory: sessionmaker[Session], draft_id: uuid.UUID) -> uuid.UUID:
    db = session_factory()
    try:
        return (
            db.execute(
                select(ComplianceFinding).where(
                    ComplianceFinding.draft_id == draft_id,
                    ComplianceFinding.status == FindingStatus.OPEN.value,
                )
            )
            .scalars()
            .first()
            .id
        )
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# WD-2 — buildable read-model truth (BM-02) + the no-draft G3 wire refusal (BM-01)
# --------------------------------------------------------------------------------------


def test_package_vm_buildable_true_after_g3_approve(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # BM-02 positive: a clean G3 approve marks the draft APPROVED, so the package_assembly VM
    # reports buildable=True (was permanently False). The normal path emits NO missing-side-effect
    # diagnostic.
    import logging

    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    draft_id, _ = _seed_compliance_draft(seeded, matter_id)
    _freeze_registry(seeded, matter_id)  # G3 approve's registry_version_match needs a frozen pin
    gate = GateState.COMPLIANCE_REVIEW.value

    with caplog.at_level(logging.ERROR, logger="clarionpi.orchestrator"):
        resp = client.post(
            f"/api/matters/{matter_id}/gates/{gate}/submit",
            json={
                "action": GateAction.APPROVE.value,
                "idempotency_key": uuid.uuid4().hex,
                "payload_version": _payload_version(seeded, matter_id),
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["to_state"] == GateState.PACKAGE_ASSEMBLY.value
    assert not [
        r
        for r in caplog.records
        if r.name == "clarionpi.orchestrator" and r.levelno == logging.ERROR
    ]

    env = client.get(f"/api/matters/{matter_id}/gates/current")
    assert env.status_code == 200, env.text
    assert env.json()["view_model"]["buildable"] is True

    db = seeded()
    try:
        assert db.get(DemandDraft, draft_id).status == DraftStatus.APPROVED.value
    finally:
        db.close()


def test_package_vm_buildable_false_outside_package_assembly(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    # BM-02 negative: buildable is only meaningful at package_assembly. An APPROVED draft on a
    # matter parked elsewhere (package_ready) is NOT buildable — the state gate wins.
    from app.api.view_models import package_vm

    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        matter.gate_state = GateState.PACKAGE_READY.value
        db.add(
            DemandDraft(
                firm_id=DEV_FIRM_ID,
                matter_id=matter.id,
                version=1,
                registry_version=matter.registry_version,
                strategy_plan_version=1,
                status=DraftStatus.APPROVED.value,
            )
        )
        db.commit()
        assert package_vm(db, matter)["buildable"] is False
    finally:
        db.close()


def test_package_vm_buildable_false_with_superseded_current_draft(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    # BM-02 edge: at package_assembly, if the highest-version draft is SUPERSEDED, latest_draft()
    # returns None (never falls back to an older draft) -> buildable stays False.
    from app.api.view_models import package_vm

    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        matter.gate_state = GateState.PACKAGE_ASSEMBLY.value
        db.add(
            DemandDraft(
                firm_id=DEV_FIRM_ID,
                matter_id=matter.id,
                version=2,
                registry_version=matter.registry_version,
                strategy_plan_version=1,
                status=DraftStatus.SUPERSEDED.value,
            )
        )
        db.commit()
        assert package_vm(db, matter)["buildable"] is False
    finally:
        db.close()


def test_g3_approve_without_current_draft_returns_exact_guard_failure(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    # BM-01 no-draft wire: guards pass (no draft -> no_blocking_findings counts zero) but the side
    # effect finds no current draft -> the route returns the exact existing-shape 409 guard_failed
    # body with guard=demand_draft, code=draft_missing (no new status/shape branch).
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.COMPLIANCE_REVIEW)
    _freeze_registry(seeded, matter_id)  # registry_version_match; no draft is seeded
    resp = client.post(
        f"/api/matters/{matter_id}/gates/{GateState.COMPLIANCE_REVIEW.value}/submit",
        json={
            "action": GateAction.APPROVE.value,
            "idempotency_key": uuid.uuid4().hex,
            "payload_version": _payload_version(seeded, matter_id),
        },
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "guard_failed"
    assert body["guard"] == "demand_draft"
    assert body["code"] == "draft_missing"
