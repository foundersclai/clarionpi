"""Matter endpoints: create happy path, typed non-AZ + out-of-scope refusals, tenant-scoped
fetch + 404, intake-flag persistence (WI-2)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import IntakeFlagAnswer
from app.models.orm import Matter

_INTAKE_FLAGS = (
    "public_entity_involved",
    "plaintiff_is_minor",
    "wrongful_death",
    "coverage_dispute",
)


def _valid_body() -> dict[str, str]:
    return {
        "client_display_name": "Jane Roe",
        "claim_type": "mva",
        "incident_date": "2026-01-15",
        "jurisdiction": "AZ",
        # WI-2: all four intake flags REQUIRED; only all-"no" is in the v1 box.
        "public_entity_involved": "no",
        "plaintiff_is_minor": "no",
        "wrongful_death": "no",
        "coverage_dispute": "no",
    }


def test_create_matter_returns_201_with_deadline_candidates(
    client: TestClient, firm_b_matter_id: uuid.UUID
) -> None:
    resp = client.post("/api/matters", json=_valid_body())
    assert resp.status_code == 201
    body = resp.json()

    assert body["client_display_name"] == "Jane Roe"
    assert body["gate_state"] == "corpus_processing"
    assert body["registry_version"] == 0

    kinds = {c["kind"]: c for c in body["deadline_candidates"]}
    # WD-1: a created matter is private-party (eligibility ⇒ public_entity_involved=NO), so the
    # public-entity notice-of-claim candidate is suppressed — SOL only. The notice-present path
    # is exercised at the unit level (test_deadlines.py) because YES/UNKNOWN can't reach the route.
    assert set(kinds) == {"sol"}
    assert kinds["sol"]["date"] == "2028-01-15"
    # Statute cites travel to the wire so the FE banner can show them.
    assert "A.R.S. § 12-542" in kinds["sol"]["statute_cite"]
    assert kinds["sol"]["verify_status"] == "unverified"
    assert kinds["sol"]["confirmed"] is False


def test_create_matter_non_az_returns_typed_422(
    client: TestClient, firm_b_matter_id: uuid.UUID
) -> None:
    body = _valid_body() | {"jurisdiction": "CA"}
    resp = client.post("/api/matters", json=body)

    assert resp.status_code == 422
    payload = resp.json()
    assert payload["error"] == "jurisdiction_unsupported"
    assert payload["supported"] == ["AZ"]
    assert "CA" in payload["detail"]


def test_get_own_matter_returns_200(client: TestClient, firm_b_matter_id: uuid.UUID) -> None:
    created = client.post("/api/matters", json=_valid_body()).json()
    resp = client.get(f"/api/matters/{created['id']}")

    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_other_firms_matter_returns_404_not_403(
    client: TestClient, firm_b_matter_id: uuid.UUID
) -> None:
    # firm_b_matter_id belongs to Firm B; the caller is Firm A's dev attorney.
    resp = client.get(f"/api/matters/{firm_b_matter_id}")

    assert resp.status_code == 404  # never 403 — no cross-tenant existence leak
    assert resp.json()["error"] == "matter_not_found"


def test_get_unknown_matter_returns_404(client: TestClient, firm_b_matter_id: uuid.UUID) -> None:
    resp = client.get(f"/api/matters/{uuid.uuid4()}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------------------
# WI-2 — pilot-intake eligibility box
# --------------------------------------------------------------------------------------


def _matter_count(session_factory: sessionmaker[Session]) -> int:
    db = session_factory()
    try:
        return db.query(Matter).count()
    finally:
        db.close()


@pytest.mark.parametrize("flag", _INTAKE_FLAGS)
def test_create_matter_flag_yes_returns_typed_422_and_writes_nothing(
    client: TestClient, session_factory: sessionmaker[Session], flag: str
) -> None:
    before = _matter_count(session_factory)
    resp = client.post("/api/matters", json=_valid_body() | {flag: "yes"})

    assert resp.status_code == 422
    payload = resp.json()
    assert payload["error"] == "matter_out_of_scope"
    assert flag in payload["detail"]
    (reason,) = payload["reasons"]
    assert reason["flag"] == flag
    assert reason["answer"] == "yes"
    # v1 scope-boundary copy — never a system error, never legal advice.
    assert "outside v1 supported scope" in reason["reason"]
    assert "existing workflow" in reason["reason"]
    assert _matter_count(session_factory) == before  # refused before any write


def test_create_matter_unknown_refuses_with_resolve_copy(client: TestClient) -> None:
    resp = client.post("/api/matters", json=_valid_body() | {"coverage_dispute": "unknown"})

    assert resp.status_code == 422
    (reason,) = resp.json()["reasons"]
    assert reason["answer"] == "unknown"
    # The refusal says exactly what unblocks creation: resolve, answer 'no', create.
    assert "then create the matter" in reason["reason"]
    assert "answered 'no'" in reason["reason"]


def test_create_matter_multiple_flags_report_every_reason(client: TestClient) -> None:
    body = _valid_body() | {"public_entity_involved": "yes", "plaintiff_is_minor": "unknown"}
    resp = client.post("/api/matters", json=body)

    assert resp.status_code == 422
    reasons = resp.json()["reasons"]
    assert [(r["flag"], r["answer"]) for r in reasons] == [
        ("public_entity_involved", "yes"),
        ("plaintiff_is_minor", "unknown"),
    ]


def test_create_matter_missing_flag_is_a_validation_422(client: TestClient) -> None:
    """The flags are REQUIRED — omitting one is a schema validation error (no silent
    default), which is FastAPI's standard 422 shape, not the typed scope refusal."""
    body = _valid_body()
    del body["wrongful_death"]
    resp = client.post("/api/matters", json=body)

    assert resp.status_code == 422
    payload = resp.json()
    assert "error" not in payload  # pydantic validation shape, not a typed refusal
    assert any("wrongful_death" in str(item.get("loc", [])) for item in payload["detail"])


def test_create_matter_persists_flags_and_returns_them(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/matters", json=_valid_body()).json()
    for flag in _INTAKE_FLAGS:
        assert created[flag] == "no"

    fetched = client.get(f"/api/matters/{created['id']}").json()
    for flag in _INTAKE_FLAGS:
        assert fetched[flag] == "no"

    db = session_factory()
    try:
        row = db.get(Matter, uuid.UUID(created["id"]))
        assert row is not None
        assert all(getattr(row, flag) == "no" for flag in _INTAKE_FLAGS)
    finally:
        db.close()


def test_legacy_unknown_matter_is_served_not_blocked(
    client: TestClient, session_factory: sessionmaker[Session], firm_b_matter_id: uuid.UUID
) -> None:
    """Eligibility is a CREATION-TIME check only: a pre-preflight row (ORM/backfill default
    'unknown') is read back fine — the flags never gate the read path or later transitions."""
    from app.api.deps import DEV_FIRM_ID  # the caller's firm (Firm A, seeded by the fixture)

    db = session_factory()
    try:
        legacy = Matter(
            firm_id=DEV_FIRM_ID,
            client_display_name="Legacy Row",
            claim_type="mva",
            incident_date=date(2025, 12, 1),
            jurisdiction="AZ",
            gate_state="facts_review",
            registry_version=0,
            sol_candidates=[],
        )
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    finally:
        db.close()

    resp = client.get(f"/api/matters/{legacy_id}")
    assert resp.status_code == 200
    body = resp.json()
    for flag in _INTAKE_FLAGS:
        assert body[flag] == "unknown"


def test_create_matter_pins_the_rule_pack(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """BUS-02: creation pins the exact pack (version + deterministic fingerprint) the
    matter's deadline/ledger/drafting work will attest to."""
    from app.rules.loader import load_pack

    resp = client.post("/api/matters", json=_valid_body() | {"client_display_name": "Pin Client"})
    assert resp.status_code == 201, resp.text
    pack = load_pack("AZ")
    db = session_factory()
    try:
        row = db.get(Matter, uuid.UUID(resp.json()["id"]))
        assert row is not None
        assert row.rule_pack_version == pack.version
        assert row.rule_pack_fingerprint == pack.fingerprint
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# WD-1 — public-entity notice-of-claim suppression on the create route (BM-02 / BM-03)
# --------------------------------------------------------------------------------------


def test_created_matter_excludes_public_entity_notice_candidate(client: TestClient) -> None:
    body = client.post("/api/matters", json=_valid_body()).json()
    kinds = {c["kind"] for c in body["deadline_candidates"]}
    assert "notice_of_claim" not in kinds


def test_created_matter_retains_sol_candidate(client: TestClient) -> None:
    body = client.post("/api/matters", json=_valid_body()).json()
    kinds = {c["kind"] for c in body["deadline_candidates"]}
    assert "sol" in kinds


def test_create_passes_intake_answer_to_deadline_computation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route threads the matter's ACTUAL intake answer into deadline computation, not a
    hardcoded constant — the wiring that keeps suppression keyed on real intake."""
    import app.api.routes.matters as matters_route
    from app.rules.deadlines import compute_deadline_candidates as real

    seen: dict = {}

    def _spy(*args, **kwargs):
        seen["public_entity_involved"] = kwargs.get(
            "public_entity_involved", args[3] if len(args) > 3 else None
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(matters_route, "compute_deadline_candidates", _spy)
    resp = client.post("/api/matters", json=_valid_body())
    assert resp.status_code == 201, resp.text
    # The body answered "no"; the route passed exactly that enum, sourced from the request.
    assert seen["public_entity_involved"] is IntakeFlagAnswer.NO


def test_create_eligibility_refusal_unchanged(client: TestClient) -> None:
    # WD-1 changes deadline computation, not the WI-2 eligibility gate: a public-entity matter
    # is still refused with the typed 422 before any deadline work happens.
    resp = client.post("/api/matters", json=_valid_body() | {"public_entity_involved": "yes"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "matter_out_of_scope"


def test_create_non_az_refusal_unchanged(client: TestClient) -> None:
    resp = client.post("/api/matters", json=_valid_body() | {"jurisdiction": "CA"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "jurisdiction_unsupported"


def test_matter_view_rehydrates_sol_only_candidates(client: TestClient) -> None:
    created = client.post("/api/matters", json=_valid_body()).json()
    fetched = client.get(f"/api/matters/{created['id']}").json()
    kinds = {c["kind"] for c in fetched["deadline_candidates"]}
    assert kinds == {"sol"}
