"""Caller-level pin-drift boundaries (BUS-02): every replaced ``load_pack`` call refuses
version/fingerprint drift TYPED at its own REST/SSE boundary — before that workflow's first
write — and changing the file back cannot conceal it (the pin, not the YAML, is authority).

Matters are created through the real API (so they are pinned to the current pack), then the
pin is corrupted in the DB to simulate a pack edited after creation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import DEV_USER_EMAIL, DEV_USER_PASSWORD, seed_dev_users
from app.core.config import get_settings
from app.models.enums import GateState
from app.models.orm import Matter, StrategyPlan

_LOGIN = {"email": DEV_USER_EMAIL, "password": DEV_USER_PASSWORD}


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


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json=_LOGIN).status_code == 200


def _create_matter(client: TestClient) -> uuid.UUID:
    resp = client.post(
        "/api/matters",
        json={
            "client_display_name": "Drift Client",
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


def _drift_pin(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    state: GateState | None = None,
) -> None:
    """Corrupt the matter's fingerprint pin (simulating a pack edited after creation)."""
    db = session_factory()
    try:
        matter = db.get(Matter, matter_id)
        assert matter is not None
        matter.rule_pack_fingerprint = "0" * 64
        if state is not None:
            matter.gate_state = state.value
        db.commit()
    finally:
        db.close()


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name: str | None = None
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name is not None:
            events.append((name, json.loads(line.split(":", 1)[1].strip())))
    return events


def test_ingest_run_refuses_drifted_pin_before_any_write(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client)
    matter_id = _create_matter(client)
    _drift_pin(seeded, matter_id)

    resp = client.post(f"/api/matters/{matter_id}/ingest/run")
    assert resp.status_code == 200, resp.text
    errors = [d for n, d in _sse_events(resp.text) if n == "error"]
    assert errors and errors[0]["error"] == "rule_pack_changed"
    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        assert matter.gate_state == GateState.CORPUS_PROCESSING.value  # untouched
        assert matter.registry_version == 0  # no registry/ledger write happened
    finally:
        db.close()


def test_analysis_run_refuses_drifted_pin_at_entry(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client)
    matter_id = _create_matter(client)
    _drift_pin(seeded, matter_id, state=GateState.ANALYSIS_RUNNING)

    resp = client.post(f"/api/matters/{matter_id}/analysis/run")
    assert resp.status_code == 200, resp.text
    errors = [d for n, d in _sse_events(resp.text) if n == "error"]
    assert errors and errors[0]["error"] == "rule_pack_changed"
    db = seeded()
    try:
        assert db.get(Matter, matter_id).gate_state == GateState.ANALYSIS_RUNNING.value
    finally:
        db.close()


def test_plan_emit_refuses_drifted_pin_before_writing_a_plan(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client)
    matter_id = _create_matter(client)
    _drift_pin(seeded, matter_id, state=GateState.PLAN_REVIEW)

    resp = client.post(f"/api/matters/{matter_id}/plan/emit")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"error": "rule_pack_changed"}
    db = seeded()
    try:
        plans = db.query(StrategyPlan).filter(StrategyPlan.matter_id == matter_id).all()
        assert plans == []  # refused BEFORE the row write
    finally:
        db.close()


def test_billing_edits_refuse_drifted_pin_before_any_edit(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client)
    matter_id = _create_matter(client)
    _drift_pin(seeded, matter_id, state=GateState.EVIDENCE_REVIEW)

    resp = client.post(
        f"/api/matters/{matter_id}/billing/edits",
        json={"edits": [{"billing_line_id": str(uuid.uuid4())}]},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"error": "rule_pack_changed"}  # pin door fires before line lookup


def test_evidence_view_shows_no_ledger_under_drifted_pin(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    """The read-only G2a envelope stays servable: the ledger is absent (None), never
    recomputed against law the matter did not attest to."""
    _login(client)
    matter_id = _create_matter(client)
    _drift_pin(seeded, matter_id, state=GateState.EVIDENCE_REVIEW)

    resp = client.get(f"/api/matters/{matter_id}/gates/current")
    assert resp.status_code == 200, resp.text
    view_model = resp.json()["view_model"]
    assert view_model["ledger"] is None
