"""THE M6 MILESTONE EXIT — the provenance round-trip over HTTP, on a real final letter.

M6 (provenance viewer + anchor integrity) is the final milestone. Its DoD (05 §1 M6): click any
fact in the G3 preview → the correct source page with a highlight, on the fixture, in <2s. E2/E3
(``tests/evals/test_tier1_anchor_integrity.py``) already prove anchor round-trip at 100% and
dead-anchor detectability at the *registry/compliance* layer. This test proves the SAME round-trip
at the **exit surface the frontend actually drives**: a matter taken all the way to
``package_ready`` (a real, rendered, compliance-passed demand letter), then — for every rendered
span — the two provenance surfaces Wave A shipped:

* ``GET /api/matters/{id}/provenance/{token_id}`` (bare id → display/outcome/anchors), and
* ``GET /api/documents/{id}/blob`` (the app-served ``application/pdf`` bytes, PHI-audited).

It reuses the M5-exit driver wholesale (``test_m5_exit_flow`` — same scripted Phase-0, same gate
arc, same helpers) to reach ``package_ready``; the M5 exit already asserts the letter/package
correctness, so this file asserts ONLY the M6 provenance contract on top of that arc, over the real
``app.main`` app:

1. drive to ``package_ready`` exactly as M5's exit does (the "final letter" story — spans exist on
   every ``DraftSection`` after ``demand/generate``; the arc writes ZERO ``phi_access`` rows).
2. pull the rendered sections over HTTP (the ``compliance_review`` gate view-model persists on the
   draft; re-read from the DB is fine — the spans are the same rows the wire serves) and collect
   every distinct BARE span ``token_id``.
3. for EVERY distinct token id: GET provenance → 200; assert the shape (``outcome``, ``anchors[]``;
   each anchor carries ``document_id`` / ``page`` / ``blob_url`` / ``page_count`` and ``bbox`` is
   ``null`` — page-level highlights at v1).
4. for the FIRST anchor of each token that has one: GET its ``blob_url`` → 200 ``application/pdf``,
   non-empty bytes. Count the fetches.
5. AUDIT: assert exactly ``fetch_count`` ``phi_access`` rows exist (the blob read is the audited PHI
   event, inv 7) AND that the provenance metadata lookups wrote NO audit rows (the pinned decision:
   the token lookup is not the PHI event — only the byte access is).
6. ROUND-TRIP: every anchor page is within ``[1, page_count]`` — 100% at the exit surface.
7. NEGATIVE probes over HTTP: a malformed id → 422 ``invalid_token_id``; a well-formed-but-unknown
   id → 404 ``token_not_found``; a cross-tenant matter → 404 (existence must not leak).
8. print a compact ``[M6 exit] ...`` trail (tokens resolved, anchors fetched, audit rows) like M5
   prints its state trail.

Synthetic data only — no PHI. Scripted providers, no ``@pytest.mark.integration`` (fast suite).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.orm import AuditEvent, DraftSection, Matter

# Reuse the M5-exit fixtures verbatim (imported so pytest resolves them for THIS test's params).
# ``seeded`` (dev-user seed over the shared engine), ``session_mode`` (AUTH_MODE=session +
# cache-clear), and ``phase0_overrides`` (tmp-dir storage + FakeOcr) are the exact harness the M5
# arc needs; ``client`` / ``session_factory`` / ``firm_b_matter_id`` come from the api conftest.
from tests.api.test_m5_exit_flow import (  # noqa: F401  (re-exported so this module's fixtures resolve)
    phase0_overrides,
    seeded,
    session_mode,
)
from tests.api.test_m5_exit_flow import test_m5_exit_full_demand_package as _drive_to_package_ready

# A well-formed bare id the fixture never mints (the matter has a small, low-ordinal registry).
_UNKNOWN_WELL_FORMED_TOKEN = "FACT_99999"
# A malformed id (bracketed / lower-case shapes are rejected too — nothing token-shaped on the
# path).
_MALFORMED_TOKEN = "[[FACT_1]]"


def _matter_at_package_ready(session_factory: sessionmaker[Session]) -> Matter:
    """The single matter the M5 arc drove to ``package_ready`` (there is exactly one)."""
    db = session_factory()
    try:
        matters = list(db.execute(select(Matter)).scalars())
        ready = [m for m in matters if m.gate_state == "package_ready"]
        assert len(ready) == 1, f"expected 1 package_ready matter, found {len(ready)}"
        db.expunge(ready[0])
        return ready[0]
    finally:
        db.close()


def _distinct_span_token_ids(
    session_factory: sessionmaker[Session], matter_id: uuid.UUID
) -> list[str]:
    """Every distinct BARE span ``token_id`` across the matter's rendered draft sections.

    The spans persist on ``DraftSection.spans`` (the same ``RenderedSpan`` rows the
    ``compliance_review`` view-model serves as ``sections[].spans[]`` on the wire — bare ids, inv
    11). ``DraftSection`` reaches the matter only through its parent ``DemandDraft`` (no
    ``matter_id`` FK), so join via the draft — the exact query the M5 exit uses. Sorted for order.
    """
    from app.models.orm import DemandDraft

    db = session_factory()
    try:
        draft = db.execute(
            select(DemandDraft).where(DemandDraft.matter_id == matter_id)
        ).scalar_one()
        sections = list(
            db.execute(select(DraftSection).where(DraftSection.draft_id == draft.id)).scalars()
        )
        ids: set[str] = set()
        for section in sections:
            for span in section.spans or []:
                # ``spans`` round-trips as a list of dicts (JSON column); ``token_id`` = bare id.
                ids.add(span["token_id"])
        return sorted(ids)
    finally:
        db.close()


def _phi_access_count(session_factory: sessionmaker[Session], firm_id: uuid.UUID) -> int:
    db = session_factory()
    try:
        return db.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.firm_id == firm_id, AuditEvent.event_kind == "phi_access")
        ).scalar_one()
    finally:
        db.close()


def _audit_count(session_factory: sessionmaker[Session], firm_id: uuid.UUID) -> int:
    db = session_factory()
    try:
        return db.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.firm_id == firm_id)
        ).scalar_one()
    finally:
        db.close()


def test_m6_exit_provenance_round_trip(
    client: TestClient,
    seeded: sessionmaker[Session],  # noqa: F811  (the imported M5 fixture)
    session_mode: None,  # noqa: F811
    phase0_overrides: object,  # noqa: F811  (LocalDiskStorage — used by the M5 arc)
    firm_b_matter_id: uuid.UUID,
    session_factory: sessionmaker[Session],
) -> None:
    session_factory = seeded  # the M5 arc seeds via this same factory over the shared engine

    # ---- 1. drive the whole M5 arc to package_ready (the "final letter"): reuse M5's exit ----
    # The M5 exit asserts the letter/package correctness; here we only need its END STATE + the
    # rendered spans it leaves behind. It writes ZERO ``phi_access`` rows (it audits
    # ``artifact_downloaded``, never ``phi_access``), so the blob-audit baseline below is clean.
    _drive_to_package_ready(
        client=client,
        seeded=seeded,
        session_mode=session_mode,
        phase0_overrides=phase0_overrides,
    )

    matter = _matter_at_package_ready(session_factory)
    matter_id = matter.id
    firm_id = matter.firm_id
    assert matter.gate_state == "package_ready"

    # The M5 arc did not touch the PHI blob route — no phi_access rows yet (the baseline).
    audit_before = _audit_count(session_factory, firm_id)
    assert _phi_access_count(session_factory, firm_id) == 0, (
        "the M5 arc must not write phi_access rows before the M6 blob probes"
    )

    # ---- 2. collect every distinct BARE span token id from the rendered draft --------------------
    token_ids = _distinct_span_token_ids(session_factory, matter_id)
    assert token_ids, "no rendered spans on the final letter — nothing to prove provenance over"

    # ---- 3+4+6. for every token: GET provenance (assert shape); fetch the first anchor's blob ----
    fetch_count = 0
    anchors_seen = 0
    pages_checked = 0
    tokens_with_anchor = 0
    for token_id in token_ids:
        prov = client.get(f"/api/matters/{matter_id}/provenance/{token_id}")
        assert prov.status_code == 200, f"{token_id}: {prov.text}"
        body = prov.json()
        # Shape: bare id echoed, an outcome, an anchors list; nothing token-shaped survives (the
        # route wire-scans the payload, so a leaked ``[[...]]`` would already have 500'd upstream).
        assert body["token_id"] == token_id
        # A 200 is never an orphan (the route 404s on ``orphan``); it is one of the resolved
        # outcomes {ok, amt_mismatch, unverified, disputed}. The exit letter passed G3, so the
        # rendered spans resolve cleanly — but the contract we assert here is just "not orphan".
        assert body["outcome"] != "orphan", body["outcome"]
        assert isinstance(body["outcome"], str) and body["outcome"], body["outcome"]
        assert isinstance(body["anchors"], list)

        first_fetched = False
        for anchor in body["anchors"]:
            anchors_seen += 1
            # Each anchor carries the server-joined fields; bbox is page-level (null) at v1.
            assert anchor["bbox"] is None, f"{token_id}: bbox must be null (page-level) at v1"
            assert anchor["document_id"] is not None, f"{token_id}: anchor missing document_id"
            page = anchor["page"]
            page_count = anchor["page_count"]
            assert isinstance(page, int) and isinstance(page_count, int)
            # 6. round-trip: the anchor page is within the target document's real bounds.
            assert 1 <= page <= page_count, (
                f"{token_id}: anchor page {page} out of 1..{page_count} at the exit surface"
            )
            pages_checked += 1
            assert anchor["blob_url"] == f"/api/documents/{anchor['document_id']}/blob"

            # 4. fetch the FIRST anchor's blob for this token → 200 application/pdf, non-empty.
            if not first_fetched:
                blob = client.get(anchor["blob_url"])
                assert blob.status_code == 200, f"{token_id} blob: {blob.text}"
                assert blob.headers["content-type"] == "application/pdf"
                assert blob.headers["content-disposition"].startswith("inline;")
                assert blob.content, f"{token_id}: empty PDF bytes served"
                fetch_count += 1
                first_fetched = True
        if first_fetched:
            tokens_with_anchor += 1

    # Non-trivial: the exit letter cites at least one anchored fact, so the blob leg fetched real
    # bytes (a round-trip over zero anchors would make the audit assertion below vacuous).
    assert fetch_count > 0, "no anchored token on the final letter — blob/audit legs vacuous"

    # ---- 5. AUDIT: exactly fetch_count phi_access rows; the provenance lookups wrote NO rows ----
    assert _phi_access_count(session_factory, firm_id) == fetch_count, (
        f"expected {fetch_count} phi_access rows (one per blob fetch), "
        f"got {_phi_access_count(session_factory, firm_id)}"
    )
    audit_after = _audit_count(session_factory, firm_id)
    assert audit_after - audit_before == fetch_count, (
        "the provenance metadata lookups must write NO audit rows — only the blob fetches do "
        f"(total audit delta {audit_after - audit_before} != {fetch_count} blob fetches)"
    )

    # ---- 7. NEGATIVE probes over the real HTTP app -----------------------------------------------
    malformed = client.get(f"/api/matters/{matter_id}/provenance/{_MALFORMED_TOKEN}")
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["error"] == "invalid_token_id"

    unknown = client.get(f"/api/matters/{matter_id}/provenance/{_UNKNOWN_WELL_FORMED_TOKEN}")
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"] == "token_not_found"

    # A well-formed token id against a CROSS-TENANT matter → 404 (existence not leaked; never 403).
    a_token = token_ids[0]
    cross = client.get(f"/api/matters/{firm_b_matter_id}/provenance/{a_token}")
    assert cross.status_code == 404, cross.text
    assert cross.json()["error"] == "matter_not_found"

    # ---- 8. the compact M6-exit trail (the evidence the integrator pastes) ----
    print(f"\n[M6 exit] matter {matter_id} @ package_ready — provenance round-trip over HTTP")
    print(
        f"[M6 exit] spans: {len(token_ids)} distinct token ids across the final letter; "
        f"{tokens_with_anchor} carry >=1 anchor"
    )
    print(
        f"[M6 exit] provenance: {len(token_ids)} token lookups (200), {anchors_seen} anchors "
        f"enriched; {pages_checked}/{pages_checked} anchor pages in-bounds (100% round-trip)"
    )
    print(
        f"[M6 exit] blobs: {fetch_count} PDF fetches (application/pdf, inline, non-empty) — "
        f"first anchor per anchored token"
    )
    print(
        f"[M6 exit] audit: {fetch_count} phi_access rows written by the blob fetches; "
        f"0 audit rows from the provenance metadata lookups (token lookup != PHI event, inv 7)"
    )
    print(
        "[M6 exit] negatives: malformed id -> 422 invalid_token_id; unknown well-formed id -> "
        "404 token_not_found; cross-tenant matter -> 404 (existence not leaked)"
    )
