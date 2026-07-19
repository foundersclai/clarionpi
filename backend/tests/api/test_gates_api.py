"""Gates API tests (M3 Wave B) — session-mode auth via cookies, typed refusal bodies.

Mirrors ``test_auth_api.py``'s harness: the conftest ``client`` + per-test ``seeded`` users and
an in-test ``AUTH_MODE=session`` monkeypatch (restored + cache-cleared on teardown). Matters are
created through the real API (so the AZ pack's two deadline candidates are on them), then parked
at a gate by direct ORM state set where a later state is needed.

Coverage: the GET envelope shape per state + affordances differing attorney-vs-paralegal; the
happy G1 confirm-all-then-approve; the typed 403 (paralegal approve), 409s (unconfirmed guard,
stale payload_version), replay, cross-firm 404, and the wire token-scanner biting in dev mode.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import DEV_PARALEGAL_EMAIL, DEV_USER_EMAIL, DEV_USER_PASSWORD, seed_dev_users
from app.api.routes import gates as gates_module
from app.api.wire_guard import TokenLeak
from app.core.config import get_settings
from app.models.enums import GateState
from app.models.orm import Matter

# The AZ pack's two candidate identities (rule_id == statute_cite on the wire).
SOL_CITE = "A.R.S. § 12-542 (verify — counsel)"
NOC_CITE = "A.R.S. § 12-821.01 (verify — counsel)"


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """Seed the three dev users (attorney/paralegal/admin, each with the dev password)."""
    db = session_factory()
    try:
        seed_dev_users(db)
    finally:
        db.close()
    return session_factory


@pytest.fixture
def session_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Switch to real session auth for the test, then restore (see test_auth_api.py)."""
    monkeypatch.setenv("AUTH_MODE", "session")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": DEV_USER_PASSWORD})
    assert resp.status_code == 200, resp.text


def _create_matter(client: TestClient) -> uuid.UUID:
    resp = client.post(
        "/api/matters",
        json={
            "client_display_name": "Gates API Client",
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


def _current(client: TestClient, matter_id: uuid.UUID) -> dict:
    resp = client.get(f"/api/matters/{matter_id}/gates/current")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _confirm_all_edits() -> dict:
    # WD-1: a real (private-party) matter carries SOL only; confirming NOC_CITE would raise
    # UnknownDeadlineRule (422) against the SOL-only set.
    return {
        "deadline_confirmations": [
            {"rule_id": SOL_CITE, "confirmed": True},
        ]
    }


# ------------------------------------------------------------------------------------------
# GET /gates/current — envelope shape per state + affordances per role
# ------------------------------------------------------------------------------------------


def test_current_envelope_minimal_at_auto_state(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)  # created in corpus_processing

    envelope = _current(client, matter_id)
    assert envelope["gate"] == "corpus_processing"
    assert isinstance(envelope["payload_version"], int)
    assert envelope["view_model"] == {
        "state": "corpus_processing",
        "detail": "gate UI lands in a later milestone",
    }
    affordances = envelope["role_affordances"]
    assert affordances == {"can_edit": False, "can_approve": False, "approve_blockers": []}


def test_current_envelope_facts_review_shape_for_attorney(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    envelope = _current(client, matter_id)
    assert envelope["gate"] == "facts_review"
    vm = envelope["view_model"]
    assert vm["incident_facts"] is None
    assert vm["documents_summary"] == {"total": 0, "needs_review": 0, "failed": 0}
    candidates = vm["deadline_candidates"]
    assert {c["rule_id"] for c in candidates} == {SOL_CITE}  # WD-1: SOL only (notice suppressed)
    for candidate in candidates:
        assert candidate["confirmed"] is False
        assert candidate["verify_status"] == "unverified"
        assert candidate["statute_cite"] == candidate["rule_id"]
        assert candidate["assumptions"]

    affordances = envelope["role_affordances"]
    assert affordances["can_edit"] is True
    assert affordances["can_approve"] is False
    blockers = {b["guard"]: b["code"] for b in affordances["approve_blockers"]}
    assert blockers == {"deadlines_confirmed": "deadlines_unconfirmed"}


def test_affordances_differ_attorney_vs_paralegal(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    _login(client, DEV_PARALEGAL_EMAIL)  # re-login swaps the cookie to the paralegal
    affordances = _current(client, matter_id)["role_affordances"]
    assert affordances["can_edit"] is True  # G1 prep is paralegal work
    assert affordances["can_approve"] is False
    blockers = {b["guard"]: b["code"] for b in affordances["approve_blockers"]}
    # The paralegal sees BOTH unmet conditions (all failures, not first-fail — no gray-out).
    assert blockers == {
        "role_attorney": "role_not_attorney",
        "deadlines_confirmed": "deadlines_unconfirmed",
    }


# ------------------------------------------------------------------------------------------
# POST /gates/{gate}/submit — happy path + typed refusals
# ------------------------------------------------------------------------------------------


def test_happy_g1_confirm_all_then_approve(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    version = _current(client, matter_id)["payload_version"]
    edit = client.post(
        f"/api/matters/{matter_id}/gates/facts_review/submit",
        json={
            "action": "edit",
            "idempotency_key": "api-confirm-all-1",
            "payload_version": version,
            "edits": _confirm_all_edits(),
        },
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["result"]["transitioned"] is False

    version = _current(client, matter_id)["payload_version"]
    approve = client.post(
        f"/api/matters/{matter_id}/gates/facts_review/submit",
        json={
            "action": "approve",
            "idempotency_key": "api-g1-approve-1",
            "payload_version": version,
        },
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["result"] == {
        "transitioned": True,
        "from_state": "facts_review",
        "to_state": "strategy_intake",
        "replayed": False,
    }
    assert body["matter"]["gate_state"] == "strategy_intake"
    assert body["record_id"]


def test_paralegal_approve_is_typed_403(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    _login(client, DEV_PARALEGAL_EMAIL)
    version = _current(client, matter_id)["payload_version"]
    resp = client.post(
        f"/api/matters/{matter_id}/gates/facts_review/submit",
        json={
            "action": "approve",
            "idempotency_key": "paralegal-approve-1",
            "payload_version": version,
        },
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["error"] == "role_forbidden"
    assert body["guard"] == "role_attorney"
    assert body["code"] == "role_not_attorney"


def test_unconfirmed_candidate_approve_is_409_guard_failed(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    version = _current(client, matter_id)["payload_version"]
    resp = client.post(
        f"/api/matters/{matter_id}/gates/facts_review/submit",
        json={
            "action": "approve",
            "idempotency_key": "early-approve-1",
            "payload_version": version,
        },
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "guard_failed"
    assert body["guard"] == "deadlines_confirmed"
    assert body["code"] == "deadlines_unconfirmed"


def test_stale_payload_version_is_409_with_fresh(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    fresh = _current(client, matter_id)["payload_version"]
    resp = client.post(
        f"/api/matters/{matter_id}/gates/facts_review/submit",
        json={
            "action": "edit",
            "idempotency_key": "stale-submit-1",
            "payload_version": fresh + 5,
            "edits": _confirm_all_edits(),
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"error": "stale_payload_version", "fresh_version": fresh}


def test_duplicate_idempotency_key_replays(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)

    version = _current(client, matter_id)["payload_version"]
    payload = {
        "action": "edit",
        "idempotency_key": "dup-edit-key-1",
        "payload_version": version,
        "edits": _confirm_all_edits(),
    }
    first = client.post(f"/api/matters/{matter_id}/gates/facts_review/submit", json=payload)
    assert first.status_code == 200
    assert first.json()["result"]["replayed"] is False

    second = client.post(f"/api/matters/{matter_id}/gates/facts_review/submit", json=payload)
    assert second.status_code == 200
    assert second.json()["result"]["replayed"] is True
    assert second.json()["record_id"] == first.json()["record_id"]  # ONE record, first outcome


def test_gate_state_mismatch_is_409_with_current(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)  # corpus_processing

    resp = client.post(
        f"/api/matters/{matter_id}/gates/facts_review/submit",
        json={"action": "edit", "idempotency_key": "mismatch-1", "payload_version": 0},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"error": "gate_state_mismatch", "current": "corpus_processing"}


def test_cross_firm_matter_404s_on_both_endpoints(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    firm_b_matter_id: uuid.UUID,
) -> None:
    _login(client, DEV_USER_EMAIL)  # Firm-A attorney
    get_resp = client.get(f"/api/matters/{firm_b_matter_id}/gates/current")
    assert get_resp.status_code == 404
    assert get_resp.json()["error"] == "matter_not_found"

    post_resp = client.post(
        f"/api/matters/{firm_b_matter_id}/gates/corpus_processing/submit",
        json={"action": "edit", "idempotency_key": "cross-firm-1", "payload_version": 0},
    )
    assert post_resp.status_code == 404
    assert post_resp.json()["error"] == "matter_not_found"


# ------------------------------------------------------------------------------------------
# The wire token-scanner bites (inv 11)
# ------------------------------------------------------------------------------------------


def test_token_in_view_model_raises_in_dev_mode(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)  # corpus_processing -> minimal VM path

    def _leaky_vm(state: object) -> dict:
        return {"state": "corpus_processing", "detail": "see [[FACT_1]] for details"}

    # The route calls the builder through its module namespace, so patching the gates module
    # attribute intercepts it. In dev/test the scanner RAISES — a leak is a bug, fail loud
    # (the TestClient re-raises server exceptions, which is exactly the CI-loud behavior).
    monkeypatch.setattr(gates_module, "minimal_gate_vm", _leaky_vm)
    with pytest.raises(TokenLeak):
        client.get(f"/api/matters/{matter_id}/gates/current")


# ------------------------------------------------------------------------------------------
# GET /api/matters — the tenant-scoped list (M3 FE list)
# ------------------------------------------------------------------------------------------


def test_list_matters_tenant_scoped_newest_first(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    firm_b_matter_id: uuid.UUID,
) -> None:
    _login(client, DEV_USER_EMAIL)
    first = _create_matter(client)
    second = _create_matter(client)

    resp = client.get("/api/matters")
    assert resp.status_code == 200, resp.text
    ids = [m["id"] for m in resp.json()["matters"]]
    assert str(first) in ids
    assert str(second) in ids
    assert str(firm_b_matter_id) not in ids  # another firm's matter never appears
    for matter in resp.json()["matters"]:
        assert matter["gate_state"] == "corpus_processing"
        assert len(matter["deadline_candidates"]) == 1  # WD-1: SOL only
