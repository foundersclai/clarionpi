"""Smoke test for the M5 Wave A model reshape: draft/section/finding ORM + M5 schemas + enums.

Builds the full schema on in-memory SQLite and exercises:

* the reshaped ``DraftSection`` (``body_tokenized`` rename, ``spans``, ``validation``,
  ``registry_version``, ``sort_order``) round-tripping,
* the reshaped ``ComplianceFinding`` (``severity`` rename, ``status`` / ``span`` / ``anchors`` /
  ``section_id`` / ``disposition``) round-tripping, with the boolean ``dispositioned`` GONE,
* the ``StrategyPlan`` approve denorm (``approved_by`` / ``approved_at``) and the ``DemandDraft``
  ``strategy_plan_version`` bind,
* every new enum's value set (``SectionValidation`` / ``FindingStatus`` / ``FindingDisposition`` /
  ``CheckKind`` / ``DraftStatus``),
* the ``JudgeFinding`` semantic-only ``check_kind`` validator (mechanical kinds rejected),
* the ``FindingActionRequest`` override-requires-reason validator,
* the ``RenderedSpan`` bare-id shape (a bracketed ``[[EX_1]]``-style string is storable — these
  are shape tests; the bare-id rule is a docstring convention, not a schema constraint),
* the migration 0007 up/down/up round-trip, and
* that the M5 package-builder deps actually installed (``docx`` / ``pypdf`` / ``openpyxl`` /
  ``reportlab`` import).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.orm import Session

from alembic import command
from app.core.tenancy import tenant_add
from app.models import enums, orm, schemas

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = sa.create_engine("sqlite://")
    orm.Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def _seed(session: Session) -> tuple[uuid.UUID, orm.Matter]:
    firm_id = uuid.uuid4()
    session.add(orm.Firm(id=firm_id, name="Acme Injury Law"))
    matter = orm.Matter(
        firm_id=firm_id,
        client_display_name="Jane Roe",
        claim_type=enums.ClaimType.MVA.value,
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=enums.GateState.DRAFTING.value,
        registry_version=3,
        sol_candidates=[],
    )
    session.add(matter)
    session.flush()
    return firm_id, matter


def _draft(session: Session, firm_id: uuid.UUID, matter: orm.Matter) -> orm.DemandDraft:
    draft = orm.DemandDraft(
        firm_id=firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=3,
    )
    session.add(draft)
    session.flush()
    return draft


def _user(session: Session, firm_id: uuid.UUID) -> orm.User:
    user = orm.User(
        firm_id=firm_id,
        email="attorney@example.com",
        display_name="Attorney",
        role=enums.UserRole.ATTORNEY.value,
    )
    session.add(user)
    session.flush()
    return user


# --------------------------------------------------------------------------------------
# DemandDraft — status default + strategy_plan_version
# --------------------------------------------------------------------------------------


def test_demand_draft_status_and_plan_version_defaults(session: Session) -> None:
    firm_id, matter = _seed(session)
    draft = _draft(session, firm_id, matter)
    session.commit()

    reloaded = session.get(orm.DemandDraft, draft.id)
    assert reloaded is not None
    # status defaults to DraftStatus.DRAFTING; strategy_plan_version defaults 0.
    assert reloaded.status == enums.DraftStatus.DRAFTING.value
    assert reloaded.strategy_plan_version == 0


def test_demand_draft_binds_plan_version(session: Session) -> None:
    firm_id, matter = _seed(session)
    draft = orm.DemandDraft(
        firm_id=firm_id,
        matter_id=matter.id,
        version=2,
        registry_version=3,
        strategy_plan_version=5,
        status=enums.DraftStatus.VALIDATED.value,
    )
    session.add(draft)
    session.commit()

    reloaded = session.get(orm.DemandDraft, draft.id)
    assert reloaded is not None
    assert reloaded.strategy_plan_version == 5
    assert reloaded.status == enums.DraftStatus.VALIDATED.value


# --------------------------------------------------------------------------------------
# DraftSection — reshaped columns
# --------------------------------------------------------------------------------------


def test_draft_section_reshape_round_trips_with_defaults(session: Session) -> None:
    firm_id, matter = _seed(session)
    draft = _draft(session, firm_id, matter)
    section = orm.DraftSection(
        firm_id=firm_id,
        draft_id=draft.id,
        section_id="liability",
        purpose="Establish fault.",
        body_tokenized="[[FACT_1]] rear-ended the plaintiff.",
    )
    session.add(section)
    session.commit()

    reloaded = session.get(orm.DraftSection, section.id)
    assert reloaded is not None
    # body_tokenized is the renamed column; the old content_tokenized attribute is gone.
    assert reloaded.body_tokenized == "[[FACT_1]] rear-ended the plaintiff."
    assert not hasattr(reloaded, "content_tokenized")
    # defaults: registry_version 0, validation retry_pending, spans [], sort_order 0.
    assert reloaded.registry_version == 0
    assert reloaded.validation == enums.SectionValidation.RETRY_PENDING.value
    assert reloaded.spans == []
    assert reloaded.sort_order == 0


def test_draft_section_persists_spans_and_validation(session: Session) -> None:
    firm_id, matter = _seed(session)
    draft = _draft(session, firm_id, matter)
    spans = [
        {"span_id": "s1", "start": 0, "end": 8, "token_id": "FACT_1"},
        {"span_id": "s2", "start": 20, "end": 27, "token_id": "AMT_2"},
    ]
    section = orm.DraftSection(
        firm_id=firm_id,
        draft_id=draft.id,
        section_id="damages_and_specials",
        body_tokenized="[[FACT_1]] and the [[AMT_2]] bill.",
        registry_version=3,
        validation=enums.SectionValidation.PASSED.value,
        spans=spans,
        sort_order=3,
    )
    session.add(section)
    session.commit()

    reloaded = session.get(orm.DraftSection, section.id)
    assert reloaded is not None
    assert reloaded.validation == enums.SectionValidation.PASSED.value
    assert reloaded.spans == spans
    assert reloaded.registry_version == 3
    assert reloaded.sort_order == 3


# --------------------------------------------------------------------------------------
# ComplianceFinding — reshaped columns
# --------------------------------------------------------------------------------------


def test_compliance_finding_reshape_round_trips_with_defaults(session: Session) -> None:
    firm_id, matter = _seed(session)
    draft = _draft(session, firm_id, matter)
    finding = orm.ComplianceFinding(
        firm_id=firm_id,
        draft_id=draft.id,
        check_kind=enums.CheckKind.ORPHAN_TOKEN.value,
        bucket=enums.FindingBucket.MECHANICAL.value,
        detail="[[FACT_9]] does not resolve.",
    )
    session.add(finding)
    session.commit()

    reloaded = session.get(orm.ComplianceFinding, finding.id)
    assert reloaded is not None
    # the boolean dispositioned column is gone; status/severity/section_id have defaults.
    assert not hasattr(reloaded, "dispositioned")
    assert reloaded.severity == enums.FindingGating.BLOCKING.value
    assert reloaded.status == enums.FindingStatus.OPEN.value
    assert reloaded.section_id == ""
    assert reloaded.registry_version == 0
    assert reloaded.anchors == []
    assert reloaded.span is None
    assert reloaded.disposition is None
    assert reloaded.disposition_by is None


def test_compliance_finding_persists_lifecycle_span_and_disposition(session: Session) -> None:
    firm_id, matter = _seed(session)
    draft = _draft(session, firm_id, matter)
    user = _user(session, firm_id)
    anchors = [{"document_id": str(uuid.uuid4()), "page": 4}]
    finding = orm.ComplianceFinding(
        firm_id=firm_id,
        draft_id=draft.id,
        section_id="liability",
        registry_version=3,
        check_kind=enums.CheckKind.STRATEGY_DRIFT.value,
        bucket=enums.FindingBucket.SEMANTIC.value,
        severity=enums.FindingGating.ADVISORY.value,
        detail="tone drifts from the approved strategy",
        anchors=anchors,
        span={"start": 10, "end": 42},
        status=enums.FindingStatus.DISPOSITIONED.value,
        disposition=enums.FindingDisposition.OVERRIDE.value,
        disposition_by=user.id,
        override_reason="attorney accepts the framing",
    )
    session.add(finding)
    session.commit()

    reloaded = session.get(orm.ComplianceFinding, finding.id)
    assert reloaded is not None
    assert reloaded.severity == enums.FindingGating.ADVISORY.value
    assert reloaded.status == enums.FindingStatus.DISPOSITIONED.value
    assert reloaded.disposition == enums.FindingDisposition.OVERRIDE.value
    assert reloaded.disposition_by == user.id
    assert reloaded.span == {"start": 10, "end": 42}
    assert reloaded.anchors == anchors


# --------------------------------------------------------------------------------------
# StrategyPlan — approve denorm
# --------------------------------------------------------------------------------------


def test_strategy_plan_approve_denorm_round_trips(session: Session) -> None:
    firm_id, matter = _seed(session)
    user = _user(session, firm_id)
    approved_at = dt.datetime(2026, 7, 6, 12, 0, tzinfo=dt.UTC)
    plan = orm.StrategyPlan(
        firm_id=firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=3,
        demand_type="open",
        approved=True,
        approved_by=user.id,
        approved_at=approved_at,
    )
    session.add(plan)
    session.commit()

    reloaded = session.get(orm.StrategyPlan, plan.id)
    assert reloaded is not None
    assert reloaded.approved is True
    assert reloaded.approved_by == user.id
    # SQLite does not preserve tzinfo on a DateTime(timezone=True) round-trip; compare the naive
    # wall-clock value (the column stores + reloads the instant correctly on Postgres).
    assert reloaded.approved_at is not None
    assert reloaded.approved_at.replace(tzinfo=None) == approved_at.replace(tzinfo=None)


def test_strategy_plan_approve_denorm_defaults_null(session: Session) -> None:
    firm_id, matter = _seed(session)
    plan = orm.StrategyPlan(
        firm_id=firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=3,
        demand_type="open",
    )
    tenant_add(session, plan, firm_id)
    session.commit()

    reloaded = session.get(orm.StrategyPlan, plan.id)
    assert reloaded is not None
    assert reloaded.approved is False
    assert reloaded.approved_by is None
    assert reloaded.approved_at is None


# --------------------------------------------------------------------------------------
# New enum value sets
# --------------------------------------------------------------------------------------


def test_section_validation_values() -> None:
    assert {v.value for v in enums.SectionValidation} == {
        "passed",
        "retry_pending",
        "surfaced_failed",
    }


def test_finding_status_values() -> None:
    assert {v.value for v in enums.FindingStatus} == {
        "open",
        "patched",
        "regenerated",
        "re_verified",
        "dispositioned",
    }


def test_finding_disposition_values() -> None:
    assert {v.value for v in enums.FindingDisposition} == {"accept", "override"}


def test_check_kind_values_and_split() -> None:
    assert {v.value for v in enums.CheckKind} == {
        "orphan_token",
        "amt_ledger_mismatch",
        "dead_anchor",
        "missing_exhibit",
        "missing_statutory_term",
        "undisposed_adverse",
        "prose_total_mismatch",
        "unsupported_causation",
        "strategy_drift",
        "tone",
    }


def test_draft_status_values() -> None:
    assert {v.value for v in enums.DraftStatus} == {
        "drafting",
        "validated",
        "in_compliance",
        "approved",
        "superseded",
    }


# --------------------------------------------------------------------------------------
# Schema validators + shapes
# --------------------------------------------------------------------------------------


def test_judge_finding_accepts_semantic_kinds() -> None:
    for kind in (
        enums.CheckKind.UNSUPPORTED_CAUSATION,
        enums.CheckKind.STRATEGY_DRIFT,
        enums.CheckKind.TONE,
    ):
        jf = schemas.JudgeFinding(check_kind=kind, section_id="liability", detail="x")
        assert jf.check_kind is kind
        # severity defaults to blocking.
        assert jf.severity is enums.FindingGating.BLOCKING


def test_judge_finding_rejects_mechanical_kinds() -> None:
    for kind in (
        enums.CheckKind.ORPHAN_TOKEN,
        enums.CheckKind.AMT_LEDGER_MISMATCH,
        enums.CheckKind.DEAD_ANCHOR,
        enums.CheckKind.MISSING_EXHIBIT,
        enums.CheckKind.MISSING_STATUTORY_TERM,
        enums.CheckKind.UNDISPOSED_ADVERSE,
        enums.CheckKind.PROSE_TOTAL_MISMATCH,
    ):
        with pytest.raises(ValidationError):
            schemas.JudgeFinding(check_kind=kind, section_id="liability", detail="x")


def test_judge_finding_batch_defaults_empty() -> None:
    assert schemas.JudgeFindingBatch().findings == []


def test_finding_action_request_override_requires_reason() -> None:
    with pytest.raises(ValidationError):
        schemas.FindingActionRequest(action="override")
    with pytest.raises(ValidationError):
        schemas.FindingActionRequest(action="override", override_reason="   ")
    ok = schemas.FindingActionRequest(action="override", override_reason="attorney judgment")
    assert ok.action == "override"


def test_finding_action_request_non_override_actions_ok() -> None:
    for action in ("patch", "regen", "accept"):
        req = schemas.FindingActionRequest(action=action)  # type: ignore[arg-type]
        assert req.action == action
        assert req.override_reason is None


def test_finding_action_request_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        schemas.FindingActionRequest(action="delete")  # type: ignore[arg-type]


def test_rendered_span_bare_id_shape() -> None:
    span = schemas.RenderedSpan(span_id="s1", start=0, end=5, token_id="FACT_3")
    assert span.token_id == "FACT_3"
    # A bracketed token-shaped string is STORABLE (the bare-id rule is a docstring convention, not a
    # schema constraint); this asserts the shape holds, per the wave spec.
    bracketed = schemas.RenderedSpan(span_id="s2", start=6, end=14, token_id="[[EX_1]]")
    assert bracketed.token_id == "[[EX_1]]"


def test_span_ref_bounds() -> None:
    assert schemas.SpanRef(start=0, end=10).end == 10
    with pytest.raises(ValidationError):
        schemas.SpanRef(start=-1, end=10)
    with pytest.raises(ValidationError):
        schemas.SpanRef(start=0, end=0)


def test_section_draft_and_memo_outputs_require_content() -> None:
    with pytest.raises(ValidationError):
        schemas.SectionDraftOutput(body_tokenized="")
    with pytest.raises(ValidationError):
        schemas.MemoOutput(memo="")
    assert schemas.PlanEmphasisOutput().emphasis_directives == []


def test_reshaped_views_load_from_orm_rows(session: Session) -> None:
    firm_id, matter = _seed(session)
    draft = _draft(session, firm_id, matter)
    section = orm.DraftSection(
        firm_id=firm_id,
        draft_id=draft.id,
        section_id="liability",
        body_tokenized="[[FACT_1]] at fault.",
        registry_version=3,
        validation=enums.SectionValidation.PASSED.value,
        spans=[{"span_id": "s1", "start": 0, "end": 8, "token_id": "FACT_1"}],
        sort_order=1,
    )
    finding = orm.ComplianceFinding(
        firm_id=firm_id,
        draft_id=draft.id,
        section_id="liability",
        registry_version=3,
        check_kind=enums.CheckKind.TONE.value,
        bucket=enums.FindingBucket.SEMANTIC.value,
        severity=enums.FindingGating.ADVISORY.value,
        detail="tone check",
        anchors=[{"document_id": str(uuid.uuid4()), "page": 2}],
        span={"start": 0, "end": 8},
        status=enums.FindingStatus.OPEN.value,
    )
    session.add_all([section, finding])
    session.commit()

    sv = schemas.DraftSectionView.model_validate(section)
    assert sv.validation is enums.SectionValidation.PASSED
    assert sv.spans[0].token_id == "FACT_1"
    # json-mode-safe (UUIDs/datetimes serialize).
    sv.model_dump(mode="json")

    fv = schemas.ComplianceFindingView.model_validate(finding)
    assert fv.severity is enums.FindingGating.ADVISORY
    assert fv.check_kind is enums.CheckKind.TONE
    assert fv.span is not None and fv.span.end == 8
    fv.model_dump(mode="json")


# --------------------------------------------------------------------------------------
# M5 package-builder deps import (proves the installs landed)
# --------------------------------------------------------------------------------------


def test_m5_package_deps_import() -> None:
    import docx  # noqa: F401
    import openpyxl  # noqa: F401
    import pypdf  # noqa: F401
    import reportlab  # noqa: F401


# --------------------------------------------------------------------------------------
# Migration 0007 round-trip
# --------------------------------------------------------------------------------------


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _columns(db_url: str, table: str) -> set[str]:
    engine = sa.create_engine(db_url)
    inspector = sa.inspect(engine)
    cols = {c["name"] for c in inspector.get_columns(table)}
    engine.dispose()
    return cols


def test_migration_0007_up_down_up_round_trip(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "m5_roundtrip.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)

    # up to head: the M5 reshape is present.
    command.upgrade(cfg, "head")
    section_cols = _columns(db_url, "draft_sections")
    finding_cols = _columns(db_url, "compliance_findings")
    draft_cols = _columns(db_url, "demand_drafts")
    plan_cols = _columns(db_url, "strategy_plans")
    assert "body_tokenized" in section_cols and "content_tokenized" not in section_cols
    assert {"registry_version", "validation", "spans", "sort_order"} <= section_cols
    assert "severity" in finding_cols and "gating" not in finding_cols
    assert "dispositioned" not in finding_cols
    assert {
        "section_id",
        "registry_version",
        "anchors",
        "span",
        "status",
        "disposition",
        "disposition_by",
    } <= finding_cols
    assert "strategy_plan_version" in draft_cols
    assert {"approved_by", "approved_at"} <= plan_cols

    # down one revision (0007 -> 0006): the placeholder shape is restored.
    command.downgrade(cfg, "0006_risk_exhibits")
    section_down = _columns(db_url, "draft_sections")
    finding_down = _columns(db_url, "compliance_findings")
    assert "content_tokenized" in section_down and "body_tokenized" not in section_down
    assert "gating" in finding_down and "severity" not in finding_down
    assert "dispositioned" in finding_down
    assert "strategy_plan_version" not in _columns(db_url, "demand_drafts")
    assert "approved_by" not in _columns(db_url, "strategy_plans")

    # up again: idempotent re-application restores the M5 shape.
    command.upgrade(cfg, "head")
    assert "body_tokenized" in _columns(db_url, "draft_sections")
    assert "severity" in _columns(db_url, "compliance_findings")
    assert "dispositioned" not in _columns(db_url, "compliance_findings")
