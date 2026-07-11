"""Evidence-workbench API tests (M4 Wave B2) — session-mode auth, typed refusals, wire safety.

Mirrors ``test_gates_api.py``'s harness: the conftest ``client`` + per-test ``seeded`` users and an
in-test ``AUTH_MODE=session`` monkeypatch. Documents / billing lines are inserted by direct ORM on
the shared engine (no upload flow needed here), and matters are parked at a gate by direct state
set. Synthetic data only — no PHI.

Coverage: the full pick → manifest flow over HTTP (incl. EX-mint), the gate-state 409 (a matter in
``facts_review`` refuses picks + billing edits), the PHI endpoint (403 paralegal / 200 attorney),
billing edits happy + 422 on a bad money string, cross-firm 404s, and the wire guarantee that the
manifest exposes bare ``exhibit_token_id`` ids and never a token-shaped ``[[EX_n]]`` string.
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
from app.core.config import get_settings
from app.models.enums import DocStatus, DocType, GateState, LedgerCategory, OverlayStatus
from app.models.orm import (
    BillingLine,
    CaseDocument,
    ChronologyRowOverlay,
    Matter,
    MedicalEncounter,
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


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": DEV_USER_PASSWORD})
    assert resp.status_code == 200, resp.text


def _create_matter(client: TestClient) -> uuid.UUID:
    resp = client.post(
        "/api/matters",
        json={
            "client_display_name": "Evidence API Client",
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


def _add_document(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    filename: str = "bill.pdf",
    page_count: int = 5,
) -> uuid.UUID:
    db = session_factory()
    try:
        doc = CaseDocument(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            doc_type=DocType.BILL.value,
            source_label=filename,
            filename=filename,
            page_count=page_count,
            dedup_status="unique",
            status=DocStatus.EXTRACTED.value,
        )
        db.add(doc)
        db.commit()
        return doc.id
    finally:
        db.close()


def _add_line(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    billed: int,
    category: LedgerCategory,
) -> uuid.UUID:
    db = session_factory()
    try:
        line = BillingLine(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            provider="Provider",
            date_of_service=_DOS,
            billed_cents=billed,
            category=category.value,
            anchor={"document_id": str(document_id), "page": 1},
        )
        db.add(line)
        db.commit()
        return line.id
    finally:
        db.close()


def _add_line_dated(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    dos: dt.date,
    billed: int,
) -> uuid.UUID:
    """A billing line with an explicit date of service (to exercise the endpoint's DOS ordering)."""
    db = session_factory()
    try:
        line = BillingLine(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            provider="Provider",
            date_of_service=dos,
            billed_cents=billed,
            category=LedgerCategory.ER.value,
            anchor={"document_id": str(document_id), "page": 1},
        )
        db.add(line)
        db.commit()
        return line.id
    finally:
        db.close()


def _add_line_with_anchor(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    anchor: dict,
) -> uuid.UUID:
    """A billing line with a caller-supplied anchor dict (to exercise the malformed-anchor path)."""
    db = session_factory()
    try:
        line = BillingLine(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            provider="Provider",
            date_of_service=_DOS,
            billed_cents=10_000,
            category=LedgerCategory.ER.value,
            anchor=anchor,
        )
        db.add(line)
        db.commit()
        return line.id
    finally:
        db.close()


def _add_encounter(
    session_factory: sessionmaker[Session],
    matter_id: uuid.UUID,
    *,
    dos: dt.date = _DOS,
    provider: str = "Dr. A",
) -> uuid.UUID:
    db = session_factory()
    try:
        enc = MedicalEncounter(
            firm_id=DEV_FIRM_ID,
            matter_id=matter_id,
            date_of_service=dos,
            provider=provider,
            facility="General Hospital",
            encounter_type="office visit",
            complaints=["neck pain"],
            findings=[],
            diagnoses=["strain"],
            procedures=[],
            work_status=None,
            narrative_tokenized="",
            anchors=[{"document_id": str(uuid.uuid4()), "page": 1}],
            merged_from=[],
            field_confidence={},
        )
        db.add(enc)
        db.commit()
        return enc.id
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# Pick -> manifest happy flow (incl. mint)
# --------------------------------------------------------------------------------------


def test_pick_then_manifest_flow_with_mint(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)  # attorney
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    doc_id = _add_document(seeded, matter_id, filename="a.pdf", page_count=5)

    # Upsert a pick.
    put = client.put(
        f"/api/matters/{matter_id}/exhibits",
        json={
            "document_id": str(doc_id),
            "include_pages": [3, 1],
            "excluded_pages": [2],
            "sort_order": 1,
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["include_pages"] == [1, 3]  # sorted + deduped
    assert body["excluded_pages"] == [2]
    assert body["phi_disposition"] == "pending"
    exhibit_id = body["id"]

    # Clear the PHI (attorney).
    phi = client.post(f"/api/exhibits/{exhibit_id}/phi", json={"disposition": "cleared"})
    assert phi.status_code == 200, phi.text
    assert phi.json()["phi_disposition"] == "cleared"

    # Manifest without mint: no token id yet.
    man = client.get(f"/api/matters/{matter_id}/manifest")
    assert man.status_code == 200, man.text
    entry = man.json()["entries"][0]
    assert entry["exhibit_token_id"] is None
    assert entry["integrity"] == "ok"
    assert entry["included_pages"] == [1, 3]
    assert man.json()["blocking"] == []
    # The manifest surfaces the Exhibit row id (the key POST /exhibits/{id}/phi is driven by), not
    # just the document_id — so the UI can drive PHI actions straight from the manifest view.
    assert entry["exhibit_id"] == exhibit_id

    # The GET is READ-ONLY at every gate (BUS-05): ?mint=true no longer mints — tokens
    # settle ONLY inside the G2a confirm side effect. The registry must not move on a GET.
    unminted_again = client.get(f"/api/matters/{matter_id}/manifest?mint=true")
    assert unminted_again.status_code == 200, unminted_again.text
    assert unminted_again.json()["entries"][0]["exhibit_token_id"] is None

    # Settle the tokens the sanctioned way (the G2a side-effect path), then re-read.
    db = seeded()
    try:
        from app.models.orm import Matter as _Matter
        from app.package.manifest import settle_exhibit_tokens

        settle_exhibit_tokens(db, matter=db.get(_Matter, matter_id))
        db.commit()
    finally:
        db.close()
    minted = client.get(f"/api/matters/{matter_id}/manifest")
    assert minted.status_code == 200, minted.text
    minted_entry = minted.json()["entries"][0]
    assert minted_entry["exhibit_token_id"] == "EX_1"


def test_manifest_response_survives_wire_guard(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    doc_id = _add_document(seeded, matter_id, page_count=3)
    put = client.put(
        f"/api/matters/{matter_id}/exhibits",
        json={"document_id": str(doc_id), "include_pages": [1]},
    )
    exhibit_id = put.json()["id"]
    client.post(f"/api/exhibits/{exhibit_id}/phi", json={"disposition": "cleared"})

    # Settle the token the sanctioned way (the G2a side-effect path) — the GET never mints.
    db = seeded()
    try:
        from app.models.orm import Matter as _Matter
        from app.package.manifest import settle_exhibit_tokens

        settle_exhibit_tokens(db, matter=db.get(_Matter, matter_id))
        db.commit()
    finally:
        db.close()
    minted = client.get(f"/api/matters/{matter_id}/manifest")
    assert minted.status_code == 200, minted.text
    # The raw response text carries the bare id but NEVER a token-shaped [[EX_n]] string.
    assert "EX_1" in minted.text
    assert "[[EX_1]]" not in minted.text
    assert "[[" not in minted.text


# --------------------------------------------------------------------------------------
# Gate-state fence (409) + PHI role (403/200) + invalid pick (422)
# --------------------------------------------------------------------------------------


def test_pick_refused_outside_evidence_review(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)
    doc_id = _add_document(seeded, matter_id)

    resp = client.put(
        f"/api/matters/{matter_id}/exhibits",
        json={"document_id": str(doc_id), "include_pages": [1]},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"error": "gate_state_mismatch", "current": "facts_review"}


def test_invalid_pick_is_422(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    doc_id = _add_document(seeded, matter_id, page_count=3)

    resp = client.put(
        f"/api/matters/{matter_id}/exhibits",
        json={"document_id": str(doc_id), "include_pages": [1, 9]},  # 9 > page_count
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_pick"
    assert resp.json()["reason"] == "page_out_of_range"


def test_phi_paralegal_403_attorney_200(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)  # attorney creates the pick
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    doc_id = _add_document(seeded, matter_id)
    put = client.put(
        f"/api/matters/{matter_id}/exhibits",
        json={"document_id": str(doc_id), "include_pages": [1]},
    )
    exhibit_id = put.json()["id"]

    # Paralegal PHI change -> 403.
    _login(client, DEV_PARALEGAL_EMAIL)
    forbidden = client.post(f"/api/exhibits/{exhibit_id}/phi", json={"disposition": "cleared"})
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"] == "role_forbidden"
    assert forbidden.json()["actual"] == "paralegal"

    # Attorney PHI change -> 200.
    _login(client, DEV_USER_EMAIL)
    ok = client.post(f"/api/exhibits/{exhibit_id}/phi", json={"disposition": "excluded"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["phi_disposition"] == "excluded"


# --------------------------------------------------------------------------------------
# Billing edits — happy + 422 money
# --------------------------------------------------------------------------------------


def test_billing_edits_happy_returns_ledger(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    doc_id = _add_document(seeded, matter_id)
    line_id = _add_line(seeded, matter_id, doc_id, billed=10_000, category=LedgerCategory.ER)

    resp = client.post(
        f"/api/matters/{matter_id}/billing/edits",
        json={
            "edits": [
                {"billing_line_id": str(line_id), "billed": "$1,234.56", "category": "imaging"}
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == {"edited": 1, "recategorized": 1, "reparsed_money_fields": 1}
    ledger = body["ledger"]
    assert ledger["grand_total"]["billed_cents"] == 123_456
    assert ledger["by_category"]["imaging"]["billed_cents"] == 123_456
    assert ledger["basis"] == "billed"
    assert ledger["demand_basis_total_cents"] == 123_456
    assert isinstance(ledger["line_set_hash"], str)


def test_billing_edits_bad_money_is_422(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    doc_id = _add_document(seeded, matter_id)
    line_id = _add_line(seeded, matter_id, doc_id, billed=10_000, category=LedgerCategory.ER)

    resp = client.post(
        f"/api/matters/{matter_id}/billing/edits",
        json={"edits": [{"billing_line_id": str(line_id), "billed": "1.234"}]},  # 3-decimal
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_money_string"


def test_billing_edits_refused_outside_evidence_review(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)  # corpus_processing
    doc_id = _add_document(seeded, matter_id)
    line_id = _add_line(seeded, matter_id, doc_id, billed=10_000, category=LedgerCategory.ER)

    resp = client.post(
        f"/api/matters/{matter_id}/billing/edits",
        json={"edits": [{"billing_line_id": str(line_id), "billed": "$1.00"}]},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "gate_state_mismatch"
    assert resp.json()["current"] == "corpus_processing"


# --------------------------------------------------------------------------------------
# Cross-firm 404s
# --------------------------------------------------------------------------------------


def test_cross_firm_matter_404s(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    firm_b_matter_id: uuid.UUID,
) -> None:
    _login(client, DEV_USER_EMAIL)  # Firm-A attorney

    put = client.put(
        f"/api/matters/{firm_b_matter_id}/exhibits",
        json={"document_id": str(uuid.uuid4()), "include_pages": [1]},
    )
    assert put.status_code == 404
    assert put.json()["error"] == "matter_not_found"

    man = client.get(f"/api/matters/{firm_b_matter_id}/manifest")
    assert man.status_code == 404
    assert man.json()["error"] == "matter_not_found"

    edits = client.post(
        f"/api/matters/{firm_b_matter_id}/billing/edits",
        json={"edits": [{"billing_line_id": str(uuid.uuid4()), "billed": "$1.00"}]},
    )
    assert edits.status_code == 404
    assert edits.json()["error"] == "matter_not_found"

    lines = client.get(f"/api/matters/{firm_b_matter_id}/billing/lines")
    assert lines.status_code == 404
    assert lines.json()["error"] == "matter_not_found"

    overlay = client.put(
        f"/api/matters/{firm_b_matter_id}/chronology/{uuid.uuid4()}/overlay",
        json={"edited_fields": {"provider_display": "Dr. X"}},
    )
    assert overlay.status_code == 404
    assert overlay.json()["error"] == "matter_not_found"


# --------------------------------------------------------------------------------------
# Billing-lines read — ordering, document_id from anchor, no gate fence
# --------------------------------------------------------------------------------------


def test_billing_lines_ordered_with_document_id(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    # A read is allowed at ANY state — this matter is still at corpus_processing (no gate fence).
    doc_id = _add_document(seeded, matter_id)
    # Insert two lines whose DOS order is the reverse of insertion order — the endpoint sorts.
    later = _add_line_dated(seeded, matter_id, doc_id, dos=dt.date(2026, 3, 1), billed=20_000)
    earlier = _add_line_dated(seeded, matter_id, doc_id, dos=dt.date(2026, 1, 1), billed=10_000)

    resp = client.get(f"/api/matters/{matter_id}/billing/lines")
    assert resp.status_code == 200, resp.text
    lines = resp.json()["lines"]
    assert [line["id"] for line in lines] == [str(earlier), str(later)]  # ordered by DOS
    first = lines[0]
    assert first["date_of_service"] == "2026-01-01"
    assert first["billed_cents"] == 10_000
    assert first["document_id"] == str(doc_id)  # parsed from the anchor
    assert first["category"] == LedgerCategory.ER.value
    # Optional cents columns are present and null when unset.
    assert first["adjusted_cents"] is None
    assert first["paid_cents"] is None


def test_billing_lines_malformed_anchor_document_id_is_null(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_USER_EMAIL)
    matter_id = _create_matter(client)
    _add_line_with_anchor(seeded, matter_id, anchor={"page": 1})  # no document_id key

    resp = client.get(f"/api/matters/{matter_id}/billing/lines")
    assert resp.status_code == 200, resp.text
    # A malformed anchor is a DISPLAY concern here (null), not the fatal MalformedAnchor the money
    # layer raises for inclusion — the line still renders.
    assert resp.json()["lines"][0]["document_id"] is None


# --------------------------------------------------------------------------------------
# Chronology overlay — closed-vocab 422, gate fence 409, happy path, cross-matter 404
# --------------------------------------------------------------------------------------


def test_overlay_happy_path_persists_and_audits(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_PARALEGAL_EMAIL)  # a paralegal preps chronology edits
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    enc_id = _add_encounter(seeded, matter_id, provider="Dr. Typo")

    resp = client.put(
        f"/api/matters/{matter_id}/chronology/{enc_id}/overlay",
        json={"edited_fields": {"provider_display": "Dr. Correct", "encounter_type": "ER"}},
    )
    assert resp.status_code == 200, resp.text
    overlay = resp.json()["overlay"]
    assert overlay["encounter_id"] == str(enc_id)
    assert overlay["edited_fields"] == {"provider_display": "Dr. Correct", "encounter_type": "ER"}
    assert overlay["status"] == OverlayStatus.APPLIED.value
    assert isinstance(overlay["base_hash_at_edit"], str) and overlay["base_hash_at_edit"]

    # The overlay row persisted (keyed by the encounter).
    db = seeded()
    try:
        rows = list(
            db.execute(
                select(ChronologyRowOverlay).where(ChronologyRowOverlay.encounter_id == enc_id)
            ).scalars()
        )
        assert len(rows) == 1
        assert rows[0].status == OverlayStatus.APPLIED.value
        assert rows[0].edited_fields == {
            "provider_display": "Dr. Correct",
            "encounter_type": "ER",
        }
    finally:
        db.close()


def test_overlay_unknown_key_is_422_invalid_edits(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_PARALEGAL_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.EVIDENCE_REVIEW)
    enc_id = _add_encounter(seeded, matter_id)

    resp = client.put(
        f"/api/matters/{matter_id}/chronology/{enc_id}/overlay",
        json={"edited_fields": {"date_of_service": "2026-02-02"}},  # DOS is the spine, not editable
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_edits"

    # A non-string value is likewise rejected as invalid_edits.
    bad_value = client.put(
        f"/api/matters/{matter_id}/chronology/{enc_id}/overlay",
        json={"edited_fields": {"provider_display": 42}},
    )
    assert bad_value.status_code == 422, bad_value.text
    assert bad_value.json()["error"] == "invalid_edits"

    # An empty edit set is rejected — clearing an overlay is out of scope at M4.
    empty = client.put(
        f"/api/matters/{matter_id}/chronology/{enc_id}/overlay",
        json={"edited_fields": {}},
    )
    assert empty.status_code == 422, empty.text
    assert empty.json()["error"] == "invalid_edits"


def test_overlay_refused_outside_evidence_review(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_PARALEGAL_EMAIL)
    matter_id = _create_matter(client)
    _park(seeded, matter_id, GateState.FACTS_REVIEW)
    enc_id = _add_encounter(seeded, matter_id)

    resp = client.put(
        f"/api/matters/{matter_id}/chronology/{enc_id}/overlay",
        json={"edited_fields": {"provider_display": "Dr. X"}},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"error": "gate_state_mismatch", "current": "facts_review"}


def test_overlay_encounter_cross_matter_404(
    client: TestClient, seeded: sessionmaker[Session], session_mode: None
) -> None:
    _login(client, DEV_PARALEGAL_EMAIL)
    matter_a = _create_matter(client)
    matter_b = _create_matter(client)
    _park(seeded, matter_a, GateState.EVIDENCE_REVIEW)
    _park(seeded, matter_b, GateState.EVIDENCE_REVIEW)
    # The encounter belongs to matter_b; editing it under matter_a's path must 404 (not cross-edit).
    enc_b = _add_encounter(seeded, matter_b)

    resp = client.put(
        f"/api/matters/{matter_a}/chronology/{enc_b}/overlay",
        json={"edited_fields": {"provider_display": "Dr. X"}},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "encounter_not_found"
