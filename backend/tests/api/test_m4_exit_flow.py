"""THE M4 MILESTONE EXIT — one readable end-to-end test over HTTP.

Exit criterion (M4 plan): a paralegal preps and an attorney confirms G2a on a fixture matter —
high-severity risk flags block the G2a confirm until dispositioned, and the exhibit picks drive a
draft binder manifest with a minted EX token. The whole arc runs through the REAL wire (session-mode
cookie login, the gates envelope, the evidence-workbench routes, typed refusals) exactly as the
frontend drives it, with role separation enforced server-side (a paralegal is refused the HIGH-flag
disposition; an attorney makes it).

The flow:

1. create the matter (attorney) → scripted Phase 0 over HTTP (`ingest/run` SSE): ONE medical doc
   (two encounters 47 days apart → a >30-day treatment gap) + ONE bill doc, driven by a
   ``ScriptedProvider`` + ``FakeOcr`` + a tmp-dir ``LocalDiskStorage`` (dependency overrides). The
   script order is load-bearing (classify-then-window per doc, docs in ``(created_at, id)`` order).
2. G1 (facts_review): confirm every deadline candidate, approve → strategy_intake.
3. G1.5 (strategy_intake): edit MMI **after** both encounters (so the gap counts — a post-MMI gap
   is expected, not adverse) + a below-threshold property-damage estimate (a second, deterministic
   MEDIUM flag), then approve → analysis_running.
4. analysis run over HTTP SSE (``NullProvider`` — deterministic flags only; ≥1 HIGH treatment gap).
5. PARALEGAL session preps: a chronology overlay (provider fix), a billing recategorize (batch
   edit), an exhibit pick (include pages). The paralegal ATTEMPTS the HIGH-flag disposition → 403.
6. ATTORNEY session: disposition the HIGH flag (``address_in_letter``). Mint the manifest — the
   exhibit's PHI is still ``pending`` and it HAS includes, so ``blocking`` is NON-empty; the
   attorney clears the PHI → ``blocking`` empties. G2a approve → plan_review + the RegistryVersion
   frozen at the current version.

Prints a compact exit trail at the end (the evidence the integrator pastes). Synthetic data only —
no PHI.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    DEV_PARALEGAL_EMAIL,
    DEV_USER_EMAIL,
    DEV_USER_PASSWORD,
    seed_dev_users,
)
from app.api.routes.ingest import get_ocr, get_provider
from app.api.routes.uploads import get_object_storage
from app.core.config import get_settings
from app.core.llm_provider import CompletionResult, NullProvider, ScriptedProvider
from app.core.storage import LocalDiskStorage
from app.core.tenancy import tenant_add
from app.corpus.ocr import FakeOcr
from app.main import app
from app.models.enums import (
    DedupStatus,
    DocStatus,
    DocType,
    FlagDetector,
    FlagDisposition,
    FlagKind,
    FlagSeverity,
    GateState,
    LedgerCategory,
    OverlayStatus,
    PhiDisposition,
)
from app.models.orm import (
    BillingLine,
    CaseDocument,
    ChronologyRowOverlay,
    GateRecord,
    Matter,
    MedicalEncounter,
    RegistryVersion,
    RiskFlag,
    User,
)
from tests.corpus.pdf_builders import build_text_pdf

# Fixture dates: two encounters 47 days apart (a >30-day treatment gap), MMI AFTER both (so the gap
# counts), property-damage estimate BELOW the 150000-cent threshold (a MEDIUM low-damage flag).
_ENC1_DOS = dt.date(2026, 2, 1)
_ENC2_DOS = dt.date(2026, 3, 20)  # 47 days after _ENC1_DOS
_MMI_DATE = "2026-04-01"  # after _ENC2_DOS
_PROPERTY_DAMAGE_CENTS = 100_000  # below settings.low_property_damage_threshold_cents (150000)


# --------------------------------------------------------------------------------------
# Fixtures — mirror the analysis/M3 harness
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


@pytest.fixture
def phase0_overrides(tmp_path: Path) -> Iterator[LocalDiskStorage]:
    """Override ingest's storage/OCR deps with a tmp-dir store + FakeOcr (provider is per-call).

    The provider is NOT overridden here — Phase 0 needs a ScriptedProvider whose script depends on
    the doc order, and the analysis run needs a NullProvider; each phase sets its own override.
    """
    storage = LocalDiskStorage(tmp_path / "storage")
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_ocr] = FakeOcr
    try:
        yield storage
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
        app.dependency_overrides.pop(get_ocr, None)


# --------------------------------------------------------------------------------------
# Small helpers — wire + scripted model replies
# --------------------------------------------------------------------------------------


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": DEV_USER_PASSWORD})
    assert resp.status_code == 200, resp.text


def _current(client: TestClient, matter_id: uuid.UUID) -> dict:
    resp = client.get(f"/api/matters/{matter_id}/gates/current")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit(client: TestClient, matter_id: uuid.UUID, gate: str, payload: dict) -> object:
    return client.post(f"/api/matters/{matter_id}/gates/{gate}/submit", json=payload)


def _sse_events(resp_text: str) -> list[str]:
    events: list[str] = []
    for frame in resp_text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("event: "):
            events.append(frame.split("\n", 1)[0].removeprefix("event: "))
    return events


def _result(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=20, output_tokens=10, cost_cents=1)


def _classify(doc_type: str) -> CompletionResult:
    return _result(json.dumps({"doc_type": doc_type, "confidence": 0.95, "rationale": "r"}))


def _two_encounters(page: int) -> CompletionResult:
    """A medical-window reply carrying TWO encounters (different DOS → they never merge)."""
    return _result(
        json.dumps(
            {
                "encounters": [
                    {
                        "date_of_service": _ENC1_DOS.isoformat(),
                        "provider": "Dr. Erner",  # mistyped on purpose — paralegal overlays a fix
                        "facility": "Mercy General",
                        "encounter_type": "ER",
                        "complaints": ["neck pain"],
                        "findings": ["tenderness"],
                        "diagnoses": ["cervical strain"],
                        "procedures": [],
                        "work_status": "light duty",
                        "anchor_pages": [page],
                        "field_confidence": {"provider": 0.6, "date_of_service": 0.9},
                    },
                    {
                        "date_of_service": _ENC2_DOS.isoformat(),
                        "provider": "Dr. Smith",
                        "facility": "Ortho Clinic",
                        "encounter_type": "office visit",
                        "complaints": ["residual stiffness"],
                        "findings": [],
                        "diagnoses": ["cervical strain"],
                        "procedures": [],
                        "work_status": "full duty",
                        "anchor_pages": [page],
                        "field_confidence": {"provider": 0.9, "date_of_service": 0.9},
                    },
                ]
            }
        )
    )


def _one_bill(page: int) -> CompletionResult:
    return _result(
        json.dumps(
            {
                "lines": [
                    {
                        "provider": "Mercy General",
                        "date_of_service": _ENC1_DOS.isoformat(),
                        "code": "99284",
                        "billed": "$2,500.00",
                        "adjusted": None,
                        "paid": None,
                        "outstanding": None,
                        "category": LedgerCategory.ER.value,
                        "anchor_page": page,
                    }
                ]
            }
        )
    )


def _make_uploaded_doc(
    db: Session,
    user: User,
    matter_id: uuid.UUID,
    storage: LocalDiskStorage,
    *,
    tag: str,
    filename: str,
) -> CaseDocument:
    """Store a 1-page synthetic PDF and create an UPLOADED, doc_type=other, unique CaseDocument."""
    key = f"matters/{matter_id}/{uuid.uuid4()}.pdf"
    storage.put(key, build_text_pdf([f"Record {tag}: patient reports neck pain after an MVA."]))
    doc = CaseDocument(
        matter_id=matter_id,
        doc_type=DocType.OTHER.value,
        source_label=filename,
        filename=filename,
        storage_key=key,
        dedup_status=DedupStatus.UNIQUE.value,
        status=DocStatus.UPLOADED.value,
    )
    tenant_add(db, doc, user.firm_id)
    db.commit()
    return doc


# --------------------------------------------------------------------------------------
# THE M4 EXIT
# --------------------------------------------------------------------------------------


def test_m4_exit_full_g2a_flow(
    client: TestClient,
    seeded: sessionmaker[Session],
    session_mode: None,
    phase0_overrides: LocalDiskStorage,
) -> None:
    storage = phase0_overrides

    # ---- create the matter (attorney, session-mode cookie login) -------------------------
    _login(client, DEV_USER_EMAIL)
    created = client.post(
        "/api/matters",
        json={
            "client_display_name": "M4 Exit Client",
            "claim_type": "mva",
            "incident_date": "2026-01-15",
            "jurisdiction": "AZ",
        },
    )
    assert created.status_code == 201, created.text
    matter_id = uuid.UUID(created.json()["id"])
    assert created.json()["gate_state"] == "corpus_processing"

    # ---- seed the two uploaded docs, then drive Phase 0 over HTTP (scripted) --------------
    db = seeded()
    try:
        attorney = db.execute(select(User).where(User.email == DEV_USER_EMAIL)).scalar_one()
        doc_med = _make_uploaded_doc(
            db, attorney, matter_id, storage, tag="MED", filename="records.pdf"
        )
        doc_bill = _make_uploaded_doc(
            db, attorney, matter_id, storage, tag="BILL", filename="bill.pdf"
        )
        # Docs process in (created_at, id) order — created_at is second-resolution on SQLite, so id
        # is the real tiebreak. Build the script to match whichever sorts first.
        ordered = sorted((doc_med, doc_bill), key=lambda d: (d.created_at, d.id))
        med_id, bill_id = doc_med.id, doc_bill.id
        script_order = [d.id for d in ordered]
    finally:
        db.close()

    def _script_for(doc_id: uuid.UUID) -> list[CompletionResult]:
        # Per doc: its classify reply THEN its single-window extractor reply (1-page doc, 1 window).
        if doc_id == med_id:
            return [_classify("medical_record"), _two_encounters(1)]
        return [_classify("bill"), _one_bill(1)]

    script: list[CompletionResult] = []
    for doc_id in script_order:
        script += _script_for(doc_id)
    app.dependency_overrides[get_provider] = lambda: ScriptedProvider(script)
    try:
        ingest = client.post(f"/api/matters/{matter_id}/ingest/run")
    finally:
        app.dependency_overrides.pop(get_provider, None)
    assert ingest.status_code == 200, ingest.text
    assert "gate_ready" in _sse_events(ingest.text)

    # Phase 0 landed: 2 encounters, 1 bill line, gate at facts_review.
    db = seeded()
    try:
        assert db.get(CaseDocument, med_id).status == DocStatus.EXTRACTED.value
        assert db.get(CaseDocument, bill_id).status == DocStatus.EXTRACTED.value
        encounters = list(
            db.execute(
                select(MedicalEncounter).where(MedicalEncounter.matter_id == matter_id)
            ).scalars()
        )
        assert len(encounters) == 2
        bills = list(
            db.execute(select(BillingLine).where(BillingLine.matter_id == matter_id)).scalars()
        )
        assert len(bills) == 1
        assert bills[0].billed_cents == 250_000  # $2,500.00 → exact cents, no float
        enc_er_id = next(e.id for e in encounters if e.date_of_service == _ENC1_DOS)
    finally:
        db.close()

    # ---- G1 (facts_review): confirm every candidate, approve → strategy_intake -----------
    envelope = _current(client, matter_id)
    assert envelope["gate"] == "facts_review"
    candidates = envelope["view_model"]["deadline_candidates"]
    assert len(candidates) == 2  # AZ pack: SOL + notice-of-claim
    confirmations = [{"rule_id": c["rule_id"], "confirmed": True} for c in candidates]
    edited = _submit(
        client,
        matter_id,
        "facts_review",
        {
            "action": "edit",
            "idempotency_key": "m4-confirm-deadlines",
            "payload_version": envelope["payload_version"],
            "edits": {"deadline_confirmations": confirmations},
        },
    )
    assert edited.status_code == 200, edited.text
    envelope = _current(client, matter_id)
    approved = _submit(
        client,
        matter_id,
        "facts_review",
        {
            "action": "approve",
            "idempotency_key": "m4-g1-approve",
            "payload_version": envelope["payload_version"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["to_state"] == "strategy_intake"

    # ---- G1.5 (strategy_intake): MMI after both encounters + below-threshold property dmg -
    envelope = _current(client, matter_id)
    assert envelope["gate"] == "strategy_intake"
    g15 = _submit(
        client,
        matter_id,
        "strategy_intake",
        {
            "action": "approve",
            "idempotency_key": "m4-g15-submit",
            "payload_version": envelope["payload_version"],
            "edits": {
                "liability_theory": "Rear-end, admitted liability.",
                "injury_framing": "Cervical strain; conservative care.",
                "anchor_amount_cents": 4_000_000,
                "mmi_date": _MMI_DATE,
                "property_damage_estimate_cents": _PROPERTY_DAMAGE_CENTS,
            },
        },
    )
    assert g15.status_code == 200, g15.text
    assert g15.json()["result"]["to_state"] == "analysis_running"

    # ---- analysis run over HTTP SSE (NullProvider → deterministic flags only) -------------
    app.dependency_overrides[get_provider] = lambda: NullProvider()
    try:
        run = client.post(f"/api/matters/{matter_id}/analysis/run")
    finally:
        app.dependency_overrides.pop(get_provider, None)
    assert run.status_code == 200, run.text
    assert "gate_ready" in _sse_events(run.text)

    # Exactly the two deterministic flags: one HIGH treatment_gap + one MEDIUM low_property_damage.
    db = seeded()
    try:
        flags = list(db.execute(select(RiskFlag).where(RiskFlag.matter_id == matter_id)).scalars())
        by_kind = {f.kind: f for f in flags}
        assert set(by_kind) == {
            FlagKind.TREATMENT_GAP.value,
            FlagKind.LOW_PROPERTY_DAMAGE.value,
        }
        gap = by_kind[FlagKind.TREATMENT_GAP.value]
        assert gap.severity == FlagSeverity.HIGH.value
        assert gap.detector == FlagDetector.DATE_MATH.value
        low = by_kind[FlagKind.LOW_PROPERTY_DAMAGE.value]
        assert low.severity == FlagSeverity.MEDIUM.value
        assert low.detector == FlagDetector.DATE_MATH.value
        assert low.anchors == []  # the one anchors-optional case (intake-derived)
        high_count = db.execute(
            select(func.count())
            .select_from(RiskFlag)
            .where(
                RiskFlag.matter_id == matter_id,
                RiskFlag.severity == FlagSeverity.HIGH.value,
                RiskFlag.disposition.is_(None),
            )
        ).scalar_one()
        assert high_count == 1
        gap_flag_id = gap.id
    finally:
        db.close()

    # ---- PARALEGAL session preps: overlay, billing recategorize, exhibit pick -------------
    _login(client, DEV_PARALEGAL_EMAIL)

    # (a) chronology overlay — fix the mistyped provider on the ER encounter.
    overlay = client.put(
        f"/api/matters/{matter_id}/chronology/{enc_er_id}/overlay",
        json={"edited_fields": {"provider_display": "Dr. Ernest"}},
    )
    assert overlay.status_code == 200, overlay.text
    assert overlay.json()["overlay"]["status"] == OverlayStatus.APPLIED.value

    # (b) billing recategorize via a batch edit — ER → imaging (reflected in by_category).
    lines = client.get(f"/api/matters/{matter_id}/billing/lines")
    assert lines.status_code == 200, lines.text
    line_id = lines.json()["lines"][0]["id"]
    recat = client.post(
        f"/api/matters/{matter_id}/billing/edits",
        json={"edits": [{"billing_line_id": line_id, "category": LedgerCategory.IMAGING.value}]},
    )
    assert recat.status_code == 200, recat.text
    ledger = recat.json()["ledger"]
    assert recat.json()["outcome"]["recategorized"] == 1
    imaging_cols = ledger["by_category"].get(LedgerCategory.IMAGING.value, {})
    assert imaging_cols.get("billed_cents") == 250_000
    assert LedgerCategory.ER.value not in ledger["by_category"]  # the recategorization moved it

    # (c) exhibit pick — include page 1 of the medical doc.
    pick = client.put(
        f"/api/matters/{matter_id}/exhibits",
        json={"document_id": str(med_id), "include_pages": [1], "sort_order": 1},
    )
    assert pick.status_code == 200, pick.text
    exhibit_id = pick.json()["id"]
    assert pick.json()["phi_disposition"] == PhiDisposition.PENDING.value

    # (d) the paralegal ATTEMPTS the HIGH-flag disposition → 403 (server-enforced role gate).
    forbidden = client.put(
        f"/api/flags/{gap_flag_id}/disposition", json={"disposition": "address_in_letter"}
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"] == "role_forbidden"
    assert forbidden.json()["actual"] == "paralegal"

    # ---- ATTORNEY session: disposition the HIGH flag ---------------------------------------
    _login(client, DEV_USER_EMAIL)
    disp = client.put(
        f"/api/flags/{gap_flag_id}/disposition",
        json={"disposition": FlagDisposition.ADDRESS_IN_LETTER.value},
    )
    assert disp.status_code == 200, disp.text
    assert disp.json()["disposition_role"] == "attorney"

    # ---- manifest: READ-ONLY at every gate (BUS-05) — no GET can mint. Pending PHI on an
    # entry WITH includes → blocking NON-empty; the token settles at G2a confirm, not here.
    manifest_resp = client.get(f"/api/matters/{matter_id}/manifest?mint=true")
    assert manifest_resp.status_code == 200, manifest_resp.text
    manifest = manifest_resp.json()
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["exhibit_token_id"] is None  # nothing minted by a GET (write-on-GET removed)
    assert "[[" not in manifest_resp.text
    assert entry["included_pages"] == [1]
    assert entry["integrity"] == "ok"
    assert entry["exhibit_id"] == exhibit_id  # the manifest surfaces the PHI-endpoint key
    assert manifest["blocking"]  # NON-empty: the exhibit has includes but PHI is still pending

    # Attorney clears the PHI → blocking empties.
    phi = client.post(
        f"/api/exhibits/{exhibit_id}/phi", json={"disposition": PhiDisposition.CLEARED.value}
    )
    assert phi.status_code == 200, phi.text
    cleared = client.get(f"/api/matters/{matter_id}/manifest")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["blocking"] == []  # PHI cleared → nothing blocks the M5 build

    # ---- G2a approve → EX tokens SETTLE inside the confirm side effect, then the registry
    # freezes at the settled version (BUS-05) → plan_review ---------------------------------
    version = _current(client, matter_id)["payload_version"]
    g2a = _submit(
        client,
        matter_id,
        "evidence_review",
        {"action": "approve", "idempotency_key": "m4-g2a-approve", "payload_version": version},
    )
    assert g2a.status_code == 200, g2a.text
    assert g2a.json()["result"]["to_state"] == GateState.PLAN_REVIEW.value

    # The confirm settled the exhibit token: a plain read-only GET now shows the bare EX id
    # (ordinal shared with the FACT/AMT tokens already minted this run — assert the SHAPE).
    settled = client.get(f"/api/matters/{matter_id}/manifest")
    assert settled.status_code == 200, settled.text
    ex_token_id = settled.json()["entries"][0]["exhibit_token_id"]
    assert re.fullmatch(r"EX_\d+", ex_token_id), ex_token_id
    assert "[[" not in settled.text

    # ---- THE EXIT ASSERTIONS: state + records + flags + overlay + ledger + freeze ----------
    assert client.get(f"/api/matters/{matter_id}").json()["gate_state"] == "plan_review"

    db = seeded()
    try:
        matter = db.get(Matter, matter_id)
        assert matter.gate_state == GateState.PLAN_REVIEW.value

        # Gate records: the exact G1-edit / G1-approve / G1.5-approve / G2a-approve trail (the
        # refused paralegal HIGH disposition wrote NO gate record — it is a flag act, not a gate
        # act). Keyed by the client-minted idempotency keys (SQLite timestamps are second-res).
        records = list(
            db.execute(select(GateRecord).where(GateRecord.matter_id == matter_id)).scalars()
        )
        trail_keys = ["m4-confirm-deadlines", "m4-g1-approve", "m4-g15-submit", "m4-g2a-approve"]
        by_key = {r.idempotency_key: r for r in records}
        assert set(by_key) == set(trail_keys)
        trail = [by_key[k] for k in trail_keys]
        assert [(r.gate, r.action) for r in trail] == [
            ("facts_review", "edit"),
            ("facts_review", "approve"),
            ("strategy_intake", "approve"),
            ("evidence_review", "approve"),
        ]

        # Flags: exactly two, by severity/detector; the HIGH gap is now dispositioned (0 open high).
        flags = list(db.execute(select(RiskFlag).where(RiskFlag.matter_id == matter_id)).scalars())
        assert len(flags) == 2
        severities = sorted(f.severity for f in flags)
        assert severities == [FlagSeverity.HIGH.value, FlagSeverity.MEDIUM.value]
        detectors = {f.detector for f in flags}
        assert detectors == {FlagDetector.DATE_MATH.value}
        gap_after = next(f for f in flags if f.kind == FlagKind.TREATMENT_GAP.value)
        assert gap_after.disposition == FlagDisposition.ADDRESS_IN_LETTER.value
        assert gap_after.disposition_role == "attorney"
        open_high_after = db.execute(
            select(func.count())
            .select_from(RiskFlag)
            .where(
                RiskFlag.matter_id == matter_id,
                RiskFlag.severity == FlagSeverity.HIGH.value,
                RiskFlag.disposition.is_(None),
            )
        ).scalar_one()
        assert open_high_after == 0

        # Overlay applied on the ER encounter (the paralegal's provider fix persisted).
        overlays = list(
            db.execute(
                select(ChronologyRowOverlay).where(ChronologyRowOverlay.matter_id == matter_id)
            ).scalars()
        )
        assert len(overlays) == 1
        assert overlays[0].encounter_id == enc_er_id
        assert overlays[0].status == OverlayStatus.APPLIED.value
        assert overlays[0].edited_fields == {"provider_display": "Dr. Ernest"}

        # Ledger recategorization persisted on the SOURCE row (ER → imaging); the specials ledger
        # is a derived view over it, so a recompute reflects the new category by_category.
        line = db.execute(
            select(BillingLine).where(BillingLine.matter_id == matter_id)
        ).scalar_one()
        assert line.category == LedgerCategory.IMAGING.value

        # The G2a-confirm side effect froze exactly one RegistryVersion, at the matter's version.
        frozen = list(
            db.execute(
                select(RegistryVersion).where(
                    RegistryVersion.matter_id == matter_id,
                    RegistryVersion.frozen.is_(True),
                )
            ).scalars()
        )
        assert len(frozen) == 1
        assert frozen[0].version == matter.registry_version

        # ---- the compact M4-exit trail summary (the evidence the integrator pastes) --------
        print(f"\nM4 EXIT TRAIL — matter {matter_id}")
        print("  corpus_processing -> facts_review (phase0: 2 docs, 2 encounters, 1 bill line)")
        for i, record in enumerate(trail, start=1):
            print(
                f"  {i}. {record.gate:<16} {record.action:<8} by {record.actor_role}"
                f"  key={record.idempotency_key}"
            )
        print(
            f"  flags: {len(flags)} "
            f"(HIGH treatment_gap [{gap_after.disposition}], MEDIUM low_property_damage);"
            f" open-high after disposition={open_high_after}"
        )
        print(
            "  refused: paralegal HIGH-flag disposition -> 403 role_forbidden;"
            " paralegal overlay + recategorize + pick accepted"
        )
        print(
            f"  manifest: {ex_token_id} minted, blocking [pending PHI] -> cleared -> [] ;"
            f" ledger ER->imaging (line.category={line.category})"
        )
        print(
            f"  final: gate_state=plan_review  RegistryVersion frozen @ v{frozen[0].version}"
            f"  payload_version={version}"
        )
    finally:
        db.close()
