"""THE M3 MILESTONE EXIT — one readable end-to-end test.

Exit criterion (M3 plan): a matter flows ``corpus_processing → facts_review →
strategy_intake → analysis_running`` with a full audit trail, and the deadline confirm is
enforced server-side (an approve over unconfirmed candidates is refused; a paralegal approve
is refused with the auth-shaped 403).

The corpus stage reuses the corpus-suite pattern (one tiny synthetic PDF, ``FakeOcr``, a
``ScriptedProvider`` classify) driven directly through ``run_phase0`` against the same
in-memory engine the API client talks to; every gate act then goes through the REAL wire —
session-mode cookie login, the gates envelope, and typed refusals — exactly as the FE will
drive it. Prints a compact trail summary at the end (the M3-exit evidence).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    DEV_PARALEGAL_EMAIL,
    DEV_USER_EMAIL,
    DEV_USER_PASSWORD,
    seed_dev_users,
)
from app.core.config import get_settings
from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.core.matter_logs import MatterRunLogger
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.corpus.ingest.phase0 import run_phase0
from app.corpus.ocr import FakeOcr
from app.engine.orchestrator.phase0_completion import handle_phase0_completion
from app.models.enums import DedupStatus, DocStatus, DocType, UserRole
from app.models.orm import AuditEvent, CaseDocument, GateRecord, Matter, StrategyInputs, User
from tests.corpus.pdf_builders import build_text_pdf

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

# Verbatim-check strategy text: leading/trailing/internal whitespace must survive untouched.
_THEORY = "  Rear-end, admitted liability.\n\tLead with the ER gap explanation.  "
_FRAMING = "Cervical strain with radicular symptoms; conservative care exhausted."


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


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": DEV_USER_PASSWORD})
    assert resp.status_code == 200, resp.text


def _current(client: TestClient, matter_id: uuid.UUID) -> dict:
    resp = client.get(f"/api/matters/{matter_id}/gates/current")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit(client: TestClient, matter_id: uuid.UUID, gate: str, payload: dict) -> object:
    return client.post(f"/api/matters/{matter_id}/gates/{gate}/submit", json=payload)


def _mini_phase0(db: Session, matter: Matter, attorney: User, tmp_path: Path) -> None:
    """One tiny doc through the real Phase-0 runner (classify=`other`, FakeOcr, no services)."""
    storage = LocalDiskStorage(tmp_path / "storage")
    key = f"matters/{matter.id}/{uuid.uuid4()}.pdf"
    storage.put(key, build_text_pdf(["Progress note: patient reports neck pain after MVA."]))
    doc = CaseDocument(
        matter_id=matter.id,
        doc_type=DocType.OTHER.value,
        source_label="record.pdf",
        filename="record.pdf",
        storage_key=key,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.UPLOADED.value,
    )
    tenant_add(db, doc, attorney.firm_id)
    db.commit()

    classify = CompletionResult(
        text='{"doc_type": "other", "confidence": 0.95, "rationale": "r"}',
        input_tokens=10,
        output_tokens=5,
        cost_cents=1,
    )
    frames = list(
        run_phase0(
            db,
            matter=matter,
            user=attorney,
            storage=storage,
            ocr=FakeOcr(),
            provider=ScriptedProvider([classify]),
            on_complete=handle_phase0_completion,
            run_logger=MatterRunLogger(matter.id, "ingest", logs_dir=tmp_path),
        )
    )
    assert frames, "phase0 yielded no frames"


def test_m3_exit_full_gate_flow_with_audit_trail(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    tmp_path: Path,
) -> None:
    # ---- create the matter through the API (attorney, session-mode cookie login) --------
    _login(client, DEV_USER_EMAIL)
    created = client.post(
        "/api/matters",
        json={
            "client_display_name": "M3 Exit Client",
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
    assert created.status_code == 201, created.text
    matter_id = uuid.UUID(created.json()["id"])
    assert created.json()["gate_state"] == "corpus_processing"
    assert len(created.json()["deadline_candidates"]) == 2  # AZ pack: SOL + notice-of-claim

    # ---- corpus_processing -> facts_review: scripted-provider mini Phase 0 --------------
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        attorney = db.execute(select(User).where(User.email == DEV_USER_EMAIL)).scalar_one()
        _mini_phase0(db, matter, attorney, tmp_path)
        db.expire_all()
        assert matter.gate_state == "facts_review"
        registry_version_after_phase0 = matter.registry_version  # AMT mints bumped it to 1
        assert registry_version_after_phase0 == 1
    finally:
        db.close()

    # ---- facts_review: candidates arrive unconfirmed; approve is REFUSED (inv 4) --------
    envelope = _current(client, matter_id)
    assert envelope["gate"] == "facts_review"
    candidates = envelope["view_model"]["deadline_candidates"]
    assert len(candidates) == 2
    assert all(c["confirmed"] is False for c in candidates)
    assert envelope["role_affordances"]["can_approve"] is False
    version = envelope["payload_version"]
    assert version == 1  # registry 1 + 0 gate records

    refused = _submit(
        client,
        matter_id,
        "facts_review",
        {"action": "approve", "idempotency_key": "exit-early-approve", "payload_version": version},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "guard_failed"
    assert refused.json()["guard"] == "deadlines_confirmed"

    # ---- attorney confirms EVERY candidate (per-candidate confirm, design D1) -----------
    confirmations = [{"rule_id": c["rule_id"], "confirmed": True} for c in candidates]
    edited = _submit(
        client,
        matter_id,
        "facts_review",
        {
            "action": "edit",
            "idempotency_key": "exit-confirm-all",
            "payload_version": version,
            "edits": {"deadline_confirmations": confirmations},
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["result"]["transitioned"] is False

    # ---- G1 approve now passes -> strategy_intake ----------------------------------------
    envelope = _current(client, matter_id)
    assert envelope["role_affordances"]["can_approve"] is True
    approved = _submit(
        client,
        matter_id,
        "facts_review",
        {
            "action": "approve",
            "idempotency_key": "exit-g1-approve",
            "payload_version": envelope["payload_version"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"] == {
        "transitioned": True,
        "from_state": "facts_review",
        "to_state": "strategy_intake",
        "replayed": False,
    }

    # ---- paralegal approve at G1.5 is REFUSED 403 (sign-off is personal, inv 8) ---------
    _login(client, DEV_PARALEGAL_EMAIL)
    envelope = _current(client, matter_id)
    assert envelope["gate"] == "strategy_intake"
    assert envelope["view_model"]["deadlines_confirmed"] is True
    paralegal_refused = _submit(
        client,
        matter_id,
        "strategy_intake",
        {
            "action": "approve",
            "idempotency_key": "exit-paralegal-approve",
            "payload_version": envelope["payload_version"],
        },
    )
    assert paralegal_refused.status_code == 403, paralegal_refused.text
    assert paralegal_refused.json()["error"] == "role_forbidden"

    # ---- attorney edits strategy + approves in ONE call -> analysis_running -------------
    _login(client, DEV_USER_EMAIL)
    envelope = _current(client, matter_id)
    submitted = _submit(
        client,
        matter_id,
        "strategy_intake",
        {
            "action": "approve",
            "idempotency_key": "exit-g15-submit",
            "payload_version": envelope["payload_version"],
            "edits": {
                "liability_theory": _THEORY,
                "injury_framing": _FRAMING,
                "anchor_amount_cents": 8_500_000,
                "mmi_date": "2026-06-01",
            },
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["result"] == {
        "transitioned": True,
        "from_state": "strategy_intake",
        "to_state": "analysis_running",
        "replayed": False,
    }

    # ---- THE EXIT ASSERTIONS: state + full trail + audit mirror + verbatim strategy -----
    matter_view = client.get(f"/api/matters/{matter_id}").json()
    assert matter_view["gate_state"] == "analysis_running"

    db = seeded()
    try:
        records = list(
            db.execute(select(GateRecord).where(GateRecord.matter_id == matter_id)).scalars()
        )
        # EXACT trail: the refused approves (guard 409, role 403) wrote NO records. Trail
        # order is keyed on the client-minted idempotency keys, not created_at — SQLite
        # timestamps are second-resolution, so three same-second rows have no stable DB order.
        trail_keys = ["exit-confirm-all", "exit-g1-approve", "exit-g15-submit"]
        assert len(records) == 3
        by_key = {r.idempotency_key: r for r in records}
        assert set(by_key) == set(trail_keys)
        trail = [by_key[k] for k in trail_keys]
        assert [(r.gate, r.action) for r in trail] == [
            ("facts_review", "edit"),
            ("facts_review", "approve"),
            ("strategy_intake", "approve"),
        ]
        for record in trail:
            assert record.actor_role == UserRole.ATTORNEY.value
            assert record.actor_id is not None
            assert _HEX64.match(record.payload_hash), record.payload_hash

        # Audit mirror: one gate_action event per record, carrying the record id.
        gate_audits = [
            e for e in db.execute(select(AuditEvent)).scalars() if e.event_kind == "gate_action"
        ]
        assert len(gate_audits) == 3
        audit_by_record = {e.payload["record_id"]: e for e in gate_audits}
        assert set(audit_by_record) == {str(r.id) for r in records}
        assert [audit_by_record[str(r.id)].payload["to_state"] for r in trail] == [
            "facts_review",
            "strategy_intake",
            "analysis_running",
        ]

        # Strategy inputs stored VERBATIM (whitespace intact) incl. the M4 pull-forward field.
        strategy = db.execute(
            select(StrategyInputs).where(StrategyInputs.matter_id == matter_id)
        ).scalar_one()
        assert strategy.liability_theory == _THEORY
        assert strategy.injury_framing == _FRAMING
        assert strategy.anchor_amount_cents == 8_500_000
        assert strategy.mmi_date is not None and strategy.mmi_date.isoformat() == "2026-06-01"

        # payload_version advanced with the trail: registry(1) + records(3).
        final_version = _current(client, matter_id)["payload_version"]
        assert final_version == registry_version_after_phase0 + len(records) == 4

        # ---- the compact M3-exit trail summary (the evidence the integrator pastes) ------
        print(f"\nM3 EXIT TRAIL — matter {matter_id}")
        print("  corpus_processing -> facts_review (phase0, 1 doc, registry v1)")
        for i, record in enumerate(trail, start=1):
            print(
                f"  {i}. {record.gate:<16} {record.action:<8} by {record.actor_role}"
                f"  key={record.idempotency_key}  hash={record.payload_hash[:12]}…"
            )
        print(
            "  refused: approve@facts_review (409 guard_failed deadlines_confirmed),"
            " approve@strategy_intake by paralegal (403 role_forbidden)"
        )
        print(
            f"  final: gate_state=analysis_running  audit gate_action×{len(gate_audits)}"
            f"  payload_version={final_version}"
        )
    finally:
        db.close()
