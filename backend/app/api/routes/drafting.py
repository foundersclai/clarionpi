"""Drafting + compliance + package routes (M5 Wave D1) — Brain-2 / G3 / package on the wire.

Thin by design (api_and_wire §4): tenancy through ``get_tenant_session`` (a cross-firm id 404s,
never 403 — existence must not leak), all domain logic in the owning engines
(:mod:`app.engine.brain2` for plan emit + demand generation, :mod:`app.engine.compliance` for the
finding lifecycle, :mod:`app.package` for the artifact set), and every NON-streaming response
passed through :func:`~app.api.wire_guard.scan_wire_payload` before it leaves (invariant 11). Gate
legality moves only through the machine (in the runners, and — for the package ``artifacts_built``
advance — here via ``machine.advance``); nothing in this module decides a transition on its own.

Endpoints:

* ``POST /api/matters/{id}/plan/emit`` — G2.5 plan emit. Fenced to ``plan_review``; ONE bounded
  Opus call, so it is NON-SSE by design (a 200 JSON plan view, not a stream).
* ``POST /api/matters/{id}/demand/generate`` — the ``drafting`` SSE run, with the compliance
  pre-check hook injected INSIDE the stream (flow_03: the judge runs before the gate advances).
* ``POST /api/findings/{id}/action`` — an attorney action on one G3 finding (patch/regen/accept/
  override), mapping the engine's typed refusals to the wire.
* ``POST /api/matters/{id}/package/build`` — the ``package_assembly`` SSE build; on success it
  advances ``(PACKAGE_ASSEMBLY, ARTIFACTS_BUILT) -> PACKAGE_READY``.
* ``GET /api/matters/{id}/artifacts`` — list the matter's artifact sets (latest first).
* ``GET /api/matters/{id}/artifacts/{set_id}/{kind}`` — stream one artifact's bytes from storage.

post_draft exception surfacing (flow_03): ``run_demand_generation`` calls the ``post_draft`` hook
WITHOUT a try/except (verified in ``app.engine.brain2.generate``), and the hook
(:func:`~app.engine.compliance.engine.compliance_post_draft_hook`) can raise ``SnapshotDrift`` /
``DraftRegistryDrift`` (``JudgeUnavailable`` is already swallowed inside ``run_compliance_pass`` as
``judge_skipped``). An unhandled raise would break the stream as a 500 mid-flight. Because this
route owns the wire boundary, :func:`_demand_stream` wraps the runner and converts a structural
escape into a trailing ERROR frame — the run's own error path — so the client always sees an SSE
error, never a torn connection.

Typed error mapping:

| condition                          | HTTP | body ``error``               |
|------------------------------------|------|------------------------------|
| matter / finding not in firm scope | 404  | ``matter_not_found`` / ``finding_not_found`` |
| wrong gate for the action          | 409  | ``gate_state_mismatch``      |
| LetterStructureMissing (plan emit) | 422  | ``letter_structure_missing`` |
| HardBlockNotDisposable             | 409  | ``hard_block_not_disposable``|
| FindingDispositionForbidden        | 403  | ``role_forbidden``           |
| DispositionReasonRequired          | 422  | ``disposition_reason_required`` |
| DispositionActionNotSupported      | 422  | ``disposition_action_not_supported`` |
| unknown artifact kind (download)   | 404  | ``artifact_not_found``       |
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_tenant_session
from app.api.routes.ingest import get_provider
from app.api.routes.uploads import get_object_storage
from app.api.sse_utils import format_sse
from app.api.view_models import artifact_sets_view
from app.api.wire_guard import scan_wire_payload
from app.core.audit import record_event
from app.core.llm_provider import LLMProvider
from app.core.llm_telemetry import MeteredLLMClient
from app.core.storage import ObjectStorage, StoredObjectNotFound
from app.engine.brain2.generate import run_demand_generation
from app.engine.brain2.plan import emit_strategy_plan
from app.engine.compliance import corrections
from app.engine.compliance.engine import (
    DispositionActionNotSupported,
    DispositionReasonRequired,
    DraftRegistryDrift,
    FindingDispositionForbidden,
    HardBlockNotDisposable,
    compliance_post_draft_hook,
    disposition_finding,
    open_blocking_count,
)
from app.engine.compliance.judge import SnapshotDrift
from app.engine.orchestrator.machine import advance
from app.models.enums import ArtifactKind, GateEvent, GateState, SseEvent
from app.models.orm import ArtifactSet, ComplianceFinding, DemandDraft, Firm, Matter, User
from app.models.schemas import ComplianceFindingView, FindingActionRequest
from app.models.schemas import StrategyPlan as StrategyPlanView
from app.package.artifacts import ArtifactTokenLeak
from app.package.binder import BinderBlocked, BinderPageMissing
from app.package.build import build_artifact_set
from app.rules.errors import (
    LetterStructureMissing,
    RulePackChanged,
    RulePackInvalid,
    RulePackUnaudited,
    RulePackUnpinned,
    RulesError,
    UnsupportedJurisdiction,
)

router = APIRouter(prefix="/api", tags=["drafting"])

# Module-level dependency singletons (ruff B008; evaluated once — see routes/matters.py).
_TenantSession = Depends(get_tenant_session)
_CurrentUser = Depends(get_current_user)
_Provider = Depends(get_provider)
_ObjectStorage = Depends(get_object_storage)

# Per-kind download metadata: the served media type + the download filename.
_ARTIFACT_MEDIA: dict[str, tuple[str, str]] = {
    ArtifactKind.LETTER_DOCX.value: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "letter.docx",
    ),
    ArtifactKind.BINDER_PDF.value: ("application/pdf", "binder.pdf"),
    ArtifactKind.CHRONOLOGY_XLSX.value: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "chronology.xlsx",
    ),
    ArtifactKind.PROVENANCE_REPORT.value: ("application/pdf", "provenance_report.pdf"),
}


def _matter_not_found(matter_id: uuid.UUID) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "matter_not_found", "detail": f"no matter {matter_id}"},
    )


def _gate_state_mismatch(matter: Matter) -> JSONResponse:
    """The same shape the gate submit uses — the FE refetch signal (409)."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": "gate_state_mismatch", "current": matter.gate_state},
    )


# --------------------------------------------------------------------------------------
# G2.5 — plan emit (NON-SSE: one bounded LLM call)
# --------------------------------------------------------------------------------------


@router.post("/matters/{matter_id}/plan/emit", response_model=None)
def post_plan_emit(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    provider: LLMProvider = _Provider,
) -> JSONResponse:
    """Emit a fresh StrategyPlan for the matter and return the plan view (G2.5 "Build plan").

    Fenced to ``plan_review`` (else ``409 gate_state_mismatch``). NON-SSE by design: the emit is a
    single bounded Opus emphasis call over the deterministic skeleton — there is no per-step stream
    to show, so a 200 JSON plan view is the honest shape. A pack with no letter skeleton →
    ``422 letter_structure_missing`` (fail loud — Brain-2 never drafts a made-up skeleton). The
    plan emits ``approved=False``; approval is the separate G2.5 gate submit.
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    if matter.gate_state != GateState.PLAN_REVIEW.value:
        return _gate_state_mismatch(matter)

    client = MeteredLLMClient(provider, session, matter.firm_id, matter.id)
    try:
        plan = emit_strategy_plan(session, client, matter=matter)
    except LetterStructureMissing as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "letter_structure_missing", "detail": str(exc)},
        )
    except RulesError as exc:
        # Pin drift / unpinned-guard refusals (BUS-02): typed, no legal-source detail.
        return JSONResponse(status_code=409, content={"error": exc.diagnostic_kind})
    plan_view = StrategyPlanView.model_validate(plan).model_dump(mode="json")
    return JSONResponse(
        status_code=200, content=scan_wire_payload({"plan": plan_view}, where="drafting.plan_emit")
    )


# --------------------------------------------------------------------------------------
# drafting — demand generation (SSE; compliance pre-check hook inside the stream)
# --------------------------------------------------------------------------------------


def _demand_stream(
    session: Session, *, matter: Matter, user: User, provider: LLMProvider
) -> Iterator[str]:
    """Wrap ``run_demand_generation`` so a structural hook escape becomes a trailing ERROR frame.

    ``run_demand_generation`` calls the ``post_draft`` hook WITHOUT a try/except (see the module
    doc), and the compliance hook can raise ``SnapshotDrift`` / ``DraftRegistryDrift``. Since this
    route owns the wire, a structural escape is converted here into the run's error path (an ERROR
    frame) rather than tearing the stream as a 500. ``JudgeUnavailable`` never reaches here — the
    pass swallows it as ``judge_skipped``.
    """
    hook = compliance_post_draft_hook(provider)
    try:
        yield from run_demand_generation(
            session, matter=matter, user=user, provider=provider, post_draft=hook
        )
    except SnapshotDrift as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "demand",
                "error": "compliance_snapshot_drift",
                "section_id": exc.section_id,
                "detail": str(exc),
            },
        )
    except DraftRegistryDrift as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {"phase": "demand", "error": "draft_registry_drift", "detail": str(exc)},
        )


@router.post("/matters/{matter_id}/demand/generate", response_model=None)
def post_demand_generate(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    provider: LLMProvider = _Provider,
) -> StreamingResponse | JSONResponse:
    """Run the Brain-2 demand generation for the matter and stream its SSE frames.

    Fenced to ``drafting`` (else ``409 gate_state_mismatch``; the runner also enforces it and would
    emit a ``wrong_gate_state`` ERROR frame, but the fence gives a clean pre-stream 409). The
    compliance pre-check hook runs INSIDE the stream before the gate advance (flow_03's judge
    pre-check); a structural hook failure surfaces as an ERROR frame (see :func:`_demand_stream`),
    never a 500. FastAPI holds the tenant session open until the stream ends.
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    if matter.gate_state != GateState.DRAFTING.value:
        return _gate_state_mismatch(matter)
    return StreamingResponse(
        _demand_stream(session, matter=matter, user=user, provider=provider),
        media_type="text/event-stream",
    )


# --------------------------------------------------------------------------------------
# G3 — finding actions (patch / regen / accept / override)
# --------------------------------------------------------------------------------------


def _finding_action_response(
    session: Session, matter: Matter, draft: DemandDraft, finding: ComplianceFinding
) -> dict:
    """The 200 body after a finding action: the refreshed finding view + the open-blocking count."""
    session.refresh(finding)
    return {
        "finding": ComplianceFindingView.model_validate(finding).model_dump(mode="json"),
        "open_blocking": open_blocking_count(session, matter=matter, draft=draft),
    }


@router.post("/findings/{finding_id}/action", response_model=None)
def post_finding_action(
    finding_id: uuid.UUID,
    body: FindingActionRequest,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    provider: LLMProvider = _Provider,
) -> JSONResponse:
    """Apply one attorney action to a G3 compliance finding; map typed refusals to the wire.

    A finding outside firm scope → ``404 finding_not_found`` (never 403). Dispatch:

    * ``patch`` → mechanical span-patch then the mandatory re-verify (deterministic; a client is
      built only when the provider is live — ``re_verify`` accepts ``None``);
    * ``regen`` → single-section regen then re-verify (needs a live client);
    * ``accept`` / ``override`` → attorney disposition (attorney-only, reasoned).

    Refusal mapping: ``HardBlockNotDisposable`` → 409 ``hard_block_not_disposable`` (a hard block
    is fixed, never dispositioned to ship); ``FindingDispositionForbidden`` → 403 ``role_forbidden``
    (inv 8); ``DispositionReasonRequired`` → 422; ``DispositionActionNotSupported`` → 422. Success
    returns the refreshed finding view + the draft's ``open_blocking`` count.
    """
    finding = session.get(ComplianceFinding, finding_id)
    if finding is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "finding_not_found", "detail": f"no finding {finding_id}"},
        )
    resolved = _matter_and_draft_for_finding(session, finding)
    if resolved is None:
        # The draft/matter chain must exist for the finding; defensive 404 (never a dangling row).
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "finding_not_found", "detail": f"no finding {finding_id}"},
        )
    matter, draft = resolved

    try:
        if body.action in ("patch", "regen"):
            _apply_correction(
                session, matter=matter, draft=draft, finding=finding, provider=provider, body=body
            )
        else:  # accept | override — attorney disposition
            disposition_finding(session, user=user, finding=finding, request=body)
    except HardBlockNotDisposable as exc:
        return JSONResponse(
            status_code=409,
            content={"error": "hard_block_not_disposable", "check_kind": exc.check_kind},
        )
    except FindingDispositionForbidden as exc:
        return JSONResponse(
            status_code=403,
            content={
                "error": "role_forbidden",
                "required": ["attorney"],
                "actual": exc.actual_role,
            },
        )
    except DispositionReasonRequired as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "disposition_reason_required", "detail": str(exc)},
        )
    except DispositionActionNotSupported as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "disposition_action_not_supported", "action": exc.action},
        )

    payload = _finding_action_response(session, matter, draft, finding)
    return JSONResponse(
        status_code=200, content=scan_wire_payload(payload, where="drafting.finding_action")
    )


def _matter_and_draft_for_finding(
    session: Session, finding: ComplianceFinding
) -> tuple[Matter, DemandDraft] | None:
    """The ``(matter, draft)`` a finding belongs to (via its draft FK), or ``None`` if broken.

    Tenancy is already enforced by the firm-scoped session (a cross-firm ``session.get`` returned
    ``None`` before we reach here); this walks finding → draft → matter, resolving both non-``None``
    for the correction / refresh surfaces (which are keyed by matter + draft).
    """
    draft = session.get(DemandDraft, finding.draft_id)
    if draft is None:
        return None
    matter = session.get(Matter, draft.matter_id)
    if matter is None:
        return None
    return matter, draft


def _apply_correction(
    session: Session,
    *,
    matter: Matter,
    draft: DemandDraft,
    finding: ComplianceFinding,
    provider: LLMProvider,
    body: FindingActionRequest,
) -> None:
    """Run a patch or regen fix, then the mandatory re-verify (the engine's "always" rule).

    ``patch`` is deterministic (no model); its re-verify is deterministic-only (a ``None`` client),
    since a mechanical splice cannot change a semantic aspect — the judge is not re-run. ``regen``
    re-drafts the section (a live client) and re-verifies WITH the judge (a fresh drafter snapshot
    reproduces, so the symmetry check is legit). The plan is loaded via
    :func:`corrections._plan_for_draft`, which raises ``PlanNotFound`` if the plan is missing (a
    broken invariant, not a user error — the draft cannot exist without its plan).
    """
    plan = corrections._plan_for_draft(session, matter=matter, draft=draft)

    if body.action == "patch":
        corrections.apply_span_patch(session, matter=matter, draft=draft, finding=finding)
        corrections.re_verify(session, None, matter=matter, plan=plan, draft=draft)
    else:  # regen
        client = MeteredLLMClient(provider, session, matter.firm_id, matter.id)
        corrections.request_section_regen(
            session, client, matter=matter, plan=plan, draft=draft, finding=finding
        )
        corrections.re_verify(session, client, matter=matter, plan=plan, draft=draft)


# --------------------------------------------------------------------------------------
# package — build (SSE) + list + download
# --------------------------------------------------------------------------------------


def _firm_name(session: Session, matter: Matter) -> str:
    """The matter's firm display name (the letter letterhead). Falls back to '' if absent."""
    firm = session.get(Firm, matter.firm_id)
    return firm.name if firm is not None else ""


def _package_stream(
    session: Session,
    storage: ObjectStorage,
    *,
    matter: Matter,
    user: User,
) -> Iterator[str]:
    """Build the artifact set and stream STATUS/ARTIFACT_READY/GATE_READY frames; advance the gate.

    Flow (flow: package_assembly): STATUS ``started`` → build the set (the latest draft) → one
    ``artifact_ready`` frame per artifact (kind + a kind-keyed download url) → advance
    ``(PACKAGE_ASSEMBLY, ARTIFACTS_BUILT) -> PACKAGE_READY`` + a ``package_ready`` audit →
    GATE_READY → STATUS ``completed`` (``reused`` + the kinds). A build-gate refusal
    (``BinderBlocked`` / ``ArtifactTokenLeak`` / ``BinderPageMissing``) is a typed ERROR frame with
    NO advance (the state is unchanged — a blocked package never ships). A missing draft is a typed
    ERROR frame too.
    """
    yield format_sse(SseEvent.STATUS, {"phase": "package", "state": "started"})

    draft = _latest_draft(session, matter=matter)
    if draft is None:
        yield format_sse(
            SseEvent.ERROR,
            {"phase": "package", "error": "no_draft", "detail": "no demand draft to package"},
        )
        return

    firm_name = _firm_name(session, matter)
    try:
        result = build_artifact_set(
            session, storage, matter=matter, draft=draft, user=user, firm_name=firm_name
        )
    except RulePackUnaudited as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "package",
                "error": "rule_pack_unaudited",
                "jurisdiction": exc.jurisdiction,
                "pack_version": exc.version,
            },
        )
        return
    except RulePackUnpinned as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "package",
                "error": "rule_pack_unpinned",
                "jurisdiction": exc.jurisdiction,
            },
        )
        return
    except RulePackChanged as exc:
        # No fingerprints on the wire — the jurisdiction alone identifies the pack.
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "package",
                "error": "rule_pack_changed",
                "jurisdiction": exc.jurisdiction,
            },
        )
        return
    except UnsupportedJurisdiction as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "package",
                "error": "jurisdiction_unsupported",
                "jurisdiction": exc.jurisdiction,
            },
        )
        return
    except RulePackInvalid:
        # No exception strings, file paths, or legal citations in the frame.
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {"phase": "package", "error": "rule_pack_invalid"},
        )
        return
    except BinderBlocked as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {"phase": "package", "error": "binder_blocked", "reasons": list(exc.reasons)},
        )
        return
    except ArtifactTokenLeak as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "package",
                "error": "artifact_token_leak",
                "section_id": exc.section_id,
                "detail": str(exc),
            },
        )
        return
    except BinderPageMissing as exc:
        session.rollback()
        yield format_sse(
            SseEvent.ERROR,
            {
                "phase": "package",
                "error": "binder_page_missing",
                "document_id": str(exc.document_id),
                "page": exc.page,
            },
        )
        return

    artifact_set = result.artifact_set
    for artifact in artifact_set.artifacts or []:
        kind = artifact["kind"]
        yield format_sse(
            SseEvent.ARTIFACT_READY,
            {
                "artifact_kind": kind,
                "url": f"/api/matters/{matter.id}/artifacts/{artifact_set.id}/{kind}",
            },
        )

    # Advance the gate (guardless ARTIFACTS_BUILT edge) + audit, in one commit.
    transition = advance(GateState.PACKAGE_ASSEMBLY, GateEvent.ARTIFACTS_BUILT)
    matter.gate_state = transition.to.value
    record_event(
        session,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind="package_ready",
        payload={
            "matter_id": str(matter.id),
            "artifact_set_id": str(artifact_set.id),
            "draft_version": artifact_set.draft_version,
            "registry_version": artifact_set.registry_version,
            "reused": result.reused,
        },
    )
    session.commit()

    yield format_sse(SseEvent.GATE_READY, {"gate": "package_ready", "matter_id": str(matter.id)})
    yield format_sse(
        SseEvent.STATUS,
        {
            "phase": "package",
            "state": "completed",
            "reused": result.reused,
            "kinds": [a["kind"] for a in (artifact_set.artifacts or [])],
        },
    )


@router.post("/matters/{matter_id}/package/build", response_model=None)
def post_package_build(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    storage: ObjectStorage = _ObjectStorage,
) -> StreamingResponse | JSONResponse:
    """Build the demand package for the matter and stream its SSE frames (advances to ready).

    Fenced to ``package_assembly`` (else ``409 gate_state_mismatch``). The build is derived from
    already-approved state (no LLM); a build-gate refusal is an ERROR frame with the state unchanged
    (see :func:`_package_stream`). FastAPI holds the tenant session open until the stream ends.
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    if matter.gate_state != GateState.PACKAGE_ASSEMBLY.value:
        return _gate_state_mismatch(matter)
    return StreamingResponse(
        _package_stream(session, storage, matter=matter, user=user),
        media_type="text/event-stream",
    )


@router.get("/matters/{matter_id}/artifacts", response_model=None)
def get_artifacts(
    matter_id: uuid.UUID,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
) -> JSONResponse:
    """List the matter's artifact sets (latest first) — a read (no gate fence, no LLM).

    Each set carries its versions + ``created_at`` and its artifacts as
    ``{kind, sha256, byte_count, url}`` (the internal ``object_key`` is not surfaced — the wire
    exposes only the kind-keyed download url).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)
    payload = {"sets": artifact_sets_view(session, matter)}
    return JSONResponse(
        status_code=200, content=scan_wire_payload(payload, where="drafting.artifacts")
    )


@router.get("/matters/{matter_id}/artifacts/{set_id}/{kind}", response_model=None)
def get_artifact_download(
    matter_id: uuid.UUID,
    set_id: uuid.UUID,
    kind: str,
    session: Session = _TenantSession,
    user: User = _CurrentUser,
    storage: ObjectStorage = _ObjectStorage,
) -> Response | JSONResponse:
    """Stream one artifact's bytes from storage, tenant-scoped, with its media type + filename.

    A cross-firm matter or a set not on the matter → ``404 artifact_not_found``. An unknown ``kind``
    (or a kind not built into this set) → ``404 artifact_not_found``. The bytes are served from the
    object store by the set's stored ``object_key`` with a ``Content-Disposition`` filename and the
    per-kind media type. The download is audited (``artifact_downloaded``). This is a raw bytes
    Response (not JSON) — it is NOT wire-scanned (binary artifact bytes are not a token surface;
    the build already scanned them via ``ArtifactTokenLeak``).
    """
    matter = session.get(Matter, matter_id)
    if matter is None:
        return _matter_not_found(matter_id)

    artifact_set = session.get(ArtifactSet, set_id)
    if artifact_set is None or artifact_set.matter_id != matter.id:
        return _artifact_not_found(set_id, kind)

    media = _ARTIFACT_MEDIA.get(kind)
    entry = next((a for a in (artifact_set.artifacts or []) if a.get("kind") == kind), None)
    if media is None or entry is None:
        return _artifact_not_found(set_id, kind)

    try:
        data = storage.get(entry["object_key"])
    except StoredObjectNotFound:
        return _artifact_not_found(set_id, kind)

    media_type, filename = media
    record_event(
        session,
        firm_id=matter.firm_id,
        actor_id=user.id,
        event_kind="artifact_downloaded",
        payload={
            "matter_id": str(matter.id),
            "artifact_set_id": str(artifact_set.id),
            "kind": kind,
        },
    )
    session.commit()
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _artifact_not_found(set_id: uuid.UUID, kind: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "artifact_not_found", "detail": f"no artifact {kind} in set {set_id}"},
    )


def _latest_draft(session: Session, *, matter: Matter) -> DemandDraft | None:
    """The matter's highest-version :class:`DemandDraft`, or ``None`` (as compliance.engine)."""
    drafts = list(
        session.execute(select(DemandDraft).where(DemandDraft.matter_id == matter.id)).scalars()
    )
    if not drafts:
        return None
    return max(drafts, key=lambda d: d.version)
