"""Matter endpoints: create happy path, typed non-AZ refusal, tenant-scoped fetch + 404."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def _valid_body() -> dict[str, str]:
    return {
        "client_display_name": "Jane Roe",
        "claim_type": "mva",
        "incident_date": "2026-01-15",
        "jurisdiction": "AZ",
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
    assert set(kinds) == {"sol", "notice_of_claim"}
    assert kinds["sol"]["date"] == "2028-01-15"
    assert kinds["notice_of_claim"]["date"] == "2026-07-14"
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
