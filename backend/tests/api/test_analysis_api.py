"""Analysis API tests (M4 Wave C) — run (SSE) + re-run, flag disposition, the G2a VM, integration.

Mirrors ``test_evidence_api.py`` / ``test_gates_api.py``: the conftest ``client`` + per-test
``seeded`` users and an in-test ``AUTH_MODE=session`` monkeypatch. Matters are created through the
real API (so the AZ pack is on them), parked at a gate by direct ORM state set, and encounters /
billing lines / risk flags are inserted by direct ORM. The analysis run's provider is overridden
with a :class:`~app.core.llm_provider.NullProvider` (the run degrades but still advances — no LLM
needed to prove the gate mechanics + the VM). Synthetic data only — no PHI.

Coverage:
- run at ``analysis_running`` streams frames + advances to ``evidence_review``;
- run at ``evidence_review`` is the re-run (PICKS_CHANGED): an ``analysis_rerun_requested`` audit
  row + the matter ends at ``evidence_review`` again;
- run at ``facts_review`` → 409 gate_state_mismatch;
- disposition route: paralegal on HIGH → typed 403; attorney → 200 with detector + disposition_role
  in the body;
- the gates envelope at ``evidence_review`` returns the full VM (chronology rows token-free — assert
  no ``[[`` anywhere), affordances show the ``high_severity_dispositioned_or_override`` blocker
  while a HIGH flag is open and clear it after disposition;
- THE INTEGRATION MOMENT: G2a approve via the gates API is refused (409 override_required) with an
  open HIGH flag → disposition → approve → matter at ``plan_review`` + the frozen RegistryVersion.
"""

from __future__ import annotations

import datetime as dt
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
from app.core.llm_provider import NullProvider
from app.main import app
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    FlagDetector,
    FlagKind,
    FlagSeverity,
    GateState,
    LedgerCategory,
)
from app.models.orm import (
    AuditEvent,
    BillingLine,
    CaseDocument,
    Matter,
    MedicalEncounter,
    RegistryVersion,
    RiskFlag,
)

_DOS = dt.date(2026, 2, 1)


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


@pytest.fixture
def null_provider() -> Iterator[None]:
    """Override the run's provider with NullProvider (the run degrades but still advances)."""
    app.dependency_overrides[get_provider] = lambda: NullProvider()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_provider, None)


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": DEV_USER_PASSWORD})
    assert resp.status_code == 200, resp.text


def _create_matter(client: TestClient) -> uuid.UUID:
    resp = client.post(
        "/api/matters",
        json={
            "client_display_name": "Analysis API Client",
            "claim_type": "mva",
            "incident_date": "2026-01-15",
            "jurisdiction": "AZ",
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


def _add_document(session_factory: sessionmaker[Session], matter_id: uuid.UUID) -> uuid.UUID:
    db = session_factory()
    try:
        doc = CaseDocument(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            doc_type=DocType.MEDICAL_RECORD.value,
            source_label="records.pdf",
            filename="records.pdf",
            page_count=10,
            dedup_status=DedupStatus.UNIQUE.value,
            status=DocStatus.EXTRACTED.value,
        )
        db.add(doc)
        db.commit()
        return doc.id
    finally:
        db.close()


def _add_encounters(
    session_factory: sessionmaker[Session], matter_id: uuid.UUID, doc_id: uuid.UUID
) -> None:
    """Two encounters 40 days apart -> a deterministic treatment-gap flag (no LLM needed)."""
    db = session_factory()
    try:
        for i, dos in enumerate((dt.date(2026, 1, 1), dt.date(2026, 2, 10)), start=1):
            db.add(
                MedicalEncounter(
                    firm_id=DEV_FIRM_ID,
                    matter_id=matter_id,
                    date_of_service=dos,
                    provider="Dr. A",
                    facility="General Hospital",
                    encounter_type="PT",
                    complaints=["neck pain"],
                    findings=[],
                    diagnoses=["whiplash"],
                    procedures=[],
                    work_status=None,
                    narrative_tokenized="",
                    anchors=[{"document_id": str(doc_id), "page": i}],
                    merged_from=[],
                    field_confidence={},
                )
            )
        db.add(
            BillingLine(
                firm_id=DEV_FIRM_ID,
                matter_id=matter_id,
                provider="General Hospital",
                date_of_service=_DOS,
                billed_cents=25_000,
                category=LedgerCategory.ER.value,
                anchor={"document_id": str(doc_id), "page": 1},
            )
        )
        db.commit()
    finally:
        db.close()


def _add_high_flag(session_factory: sessionmaker[Session], matter_id: uuid.UUID) -> uuid.UUID:
    db = session_factory()
    try:
        flag = RiskFlag(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            kind=FlagKind.PREEXISTING_CONDITION.value,
            severity=FlagSeverity.HIGH.value,
            detector=FlagDetector.HEURISTIC_LLM.value,
            anchors=[{"document_id": str(uuid.uuid4()), "page": 1}],
            detail="prior neck injury",
        )
        db.add(flag)
        db.commit()
        return flag.id
    finally:
        db.close()


def _current(client: TestClient, matter_id: uuid.UUID) -> dict:
    resp = client.get(f"/api/matters/{matter_id}/gates/current")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sse_events(resp_text: str) -> list[str]:
    events: list[str] = []
    for frame in resp_text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("event: "):
            events.append(frame.split("\n", 1)[0].removeprefix("event: "))
    return events


def _audit_kinds(session_factory: sessionmaker[Session]) -> list[str]:
    db = session_factory()
    try:
        return list(db.scalars(select(AuditEvent.event_kind)))
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# Run at analysis_running — streams + advances
# --------------------------------------------------------------------------------------


def test_run_at_analysis_running_streams_and_advances(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, null_provider: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.ANALYSIS_RUNNING)
    doc_id = _add_document(seeded, matter_id)
    _add_encounters(seeded, matter_id, doc_id)

    resp = client.post(f"/api/matters/{matter_id}/analysis/run")
    assert resp.status_code == 200, resp.text
    events = _sse_events(resp.text)
    assert events[0] == "status"  # started
    assert "gate_ready" in events
    assert events[-1] == "status"  # completed
    # Matter advanced.
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        assert matter.gate_state == GateState.EVIDENCE_REVIEW.value
    finally:
        db.close()
    assert "analysis_completed" in _audit_kinds(seeded)


# --------------------------------------------------------------------------------------
# Run at evidence_review — the re-run (PICKS_CHANGED) round trip
# --------------------------------------------------------------------------------------


def test_run_at_evidence_review_is_rerun(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, null_provider: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    doc_id = _add_document(seeded, matter_id)
    _add_encounters(seeded, matter_id, doc_id)

    resp = client.post(f"/api/matters/{matter_id}/analysis/run")
    assert resp.status_code == 200, resp.text
    events = _sse_events(resp.text)
    assert "gate_ready" in events  # re-ran the build, re-advanced

    # The re-run edge was audited, and the matter is at evidence_review again.
    assert "analysis_rerun_requested" in _audit_kinds(seeded)
    db = seeded()
    try:
        assert db.get(Matter, matter_id).gate_state == GateState.EVIDENCE_REVIEW.value
    finally:
        db.close()


def test_run_at_facts_review_is_409(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, null_provider: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    resp = client.post(f"/api/matters/{matter_id}/analysis/run")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"error": "gate_state_mismatch", "current": "facts_review"}


def test_run_cross_firm_404(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    null_provider: None,
    firm_b_matter_id: uuid.UUID,
) -> None:
    _login(client, DEV_USER_EMAIL)
    resp = client.post(f"/api/matters/{firm_b_matter_id}/analysis/run")
    assert resp.status_code == 404
    assert resp.json()["error"] == "matter_not_found"


# --------------------------------------------------------------------------------------
# Flag disposition — paralegal 403 (typed) / attorney 200 (detector + disposition_role)
# --------------------------------------------------------------------------------------


def test_disposition_paralegal_high_403_attorney_200(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    flag_id = _add_high_flag(seeded, matter_id)

    # Paralegal on a HIGH flag -> typed 403.
    _login(client, DEV_PARALEGAL_EMAIL)
    forbidden = client.put(
        f"/api/flags/{flag_id}/disposition", json={"disposition": "address_in_letter"}
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"] == "role_forbidden"
    assert forbidden.json()["required"] == ["attorney"]
    assert forbidden.json()["actual"] == "paralegal"

    # Attorney -> 200 with the extended view fields.
    _login(client, DEV_USER_EMAIL)
    ok = client.put(f"/api/flags/{flag_id}/disposition", json={"disposition": "address_in_letter"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["disposition"] == "address_in_letter"
    assert body["disposition_role"] == "attorney"
    assert body["detector"] == FlagDetector.HEURISTIC_LLM.value


def test_disposition_flag_not_found_404(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    resp = client.put(
        f"/api/flags/{uuid.uuid4()}/disposition", json={"disposition": "address_in_letter"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "flag_not_found"


# --------------------------------------------------------------------------------------
# The gates envelope at evidence_review — full VM, token-free, affordance blocker
# --------------------------------------------------------------------------------------


def test_evidence_review_envelope_full_vm_token_free_and_blocker(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, null_provider: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.ANALYSIS_RUNNING)
    doc_id = _add_document(seeded, matter_id)
    _add_encounters(seeded, matter_id, doc_id)
    # Run the analysis so the VM has real chronology + ledger + flags (the gap flag is HIGH).
    run = client.post(f"/api/matters/{matter_id}/analysis/run")
    assert run.status_code == 200, run.text

    envelope = _current(client, matter_id)
    assert envelope["gate"] == GateState.EVIDENCE_REVIEW.value
    vm = envelope["view_model"]
    # Full VM shape.
    assert set(vm) == {"chronology", "ledger", "risk_flags", "exhibits", "dedup_pending"}
    assert vm["chronology"]["conflicts"] == 0
    assert vm["chronology"]["parked"] == 0
    assert isinstance(vm["chronology"]["rows"], list)
    assert vm["ledger"]["grand_total"]["billed_cents"] == 25_000
    assert vm["ledger"]["basis"] in {"billed", "paid"}
    assert "missing_paid_line_ids" in vm["ledger"]
    assert vm["exhibits"] == {"entries": [], "blocking": []}  # no picks yet
    assert vm["dedup_pending"] == 0
    # A HIGH treatment-gap flag exists and carries the extended fields.
    assert vm["risk_flags"], "the treatment gap should have produced a HIGH flag"
    gap = vm["risk_flags"][0]
    assert gap["severity"] == FlagSeverity.HIGH.value
    assert gap["detector"] == FlagDetector.DATE_MATH.value
    assert gap["disposition_role"] is None  # not yet dispositioned

    # Wire safety: NOTHING token-shaped survives anywhere in the raw envelope text.
    raw = client.get(f"/api/matters/{matter_id}/gates/current").text
    assert "[[" not in raw

    # Affordance blocker present while the HIGH flag is open.
    codes = [b["guard"] for b in envelope["role_affordances"]["approve_blockers"]]
    assert "high_severity_dispositioned_or_override" in codes
    assert envelope["role_affordances"]["can_approve"] is False

    # Disposition the HIGH flag -> the blocker clears.
    flag_id = gap["id"]
    ok = client.put(f"/api/flags/{flag_id}/disposition", json={"disposition": "address_in_letter"})
    assert ok.status_code == 200, ok.text
    after = _current(client, matter_id)
    codes_after = [b["guard"] for b in after["role_affordances"]["approve_blockers"]]
    assert "high_severity_dispositioned_or_override" not in codes_after
    assert after["role_affordances"]["can_approve"] is True


# --------------------------------------------------------------------------------------
# THE INTEGRATION MOMENT — G2a approve refused (override_required) -> disposition -> approve
# --------------------------------------------------------------------------------------


def test_g2a_approve_blocked_by_open_high_then_disposition_then_approve_freezes(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None, null_provider: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.ANALYSIS_RUNNING)
    doc_id = _add_document(seeded, matter_id)
    _add_encounters(seeded, matter_id, doc_id)
    run = client.post(f"/api/matters/{matter_id}/analysis/run")
    assert run.status_code == 200, run.text

    # Approve with an open HIGH flag -> 409 override_required (the G2a-confirm guard bites).
    version = _current(client, matter_id)["payload_version"]
    blocked = client.post(
        f"/api/matters/{matter_id}/gates/evidence_review/submit",
        json={
            "action": "approve",
            "idempotency_key": "g2a-approve-blocked-1",
            "payload_version": version,
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"] == "override_required"

    # Disposition the HIGH flag.
    gap = _current(client, matter_id)["view_model"]["risk_flags"][0]
    disp = client.put(
        f"/api/flags/{gap['id']}/disposition", json={"disposition": "address_in_letter"}
    )
    assert disp.status_code == 200, disp.text

    # Approve now succeeds -> plan_review, and the M3 side-effect froze the registry version.
    version2 = _current(client, matter_id)["payload_version"]
    approve = client.post(
        f"/api/matters/{matter_id}/gates/evidence_review/submit",
        json={
            "action": "approve",
            "idempotency_key": "g2a-approve-ok-1",
            "payload_version": version2,
        },
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["result"]["to_state"] == GateState.PLAN_REVIEW.value

    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        assert matter.gate_state == GateState.PLAN_REVIEW.value
        frozen = (
            db.execute(
                select(RegistryVersion).where(
                    RegistryVersion.matter_id == matter_id,
                    RegistryVersion.frozen.is_(True),
                )
            )
            .scalars()
            .all()
        )
        assert len(frozen) == 1  # the freeze side-effect fired
        assert frozen[0].version == matter.registry_version
    finally:
        db.close()
