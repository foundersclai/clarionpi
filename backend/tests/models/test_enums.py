"""Enum contract tests — value sets, ordering, and StrEnum-ness."""

from __future__ import annotations

from enum import StrEnum

import pytest

from app.models import enums


def test_gate_state_has_exactly_ten_values_in_order() -> None:
    assert [s.value for s in enums.GateState] == [
        "corpus_processing",
        "facts_review",
        "strategy_intake",
        "analysis_running",
        "evidence_review",
        "plan_review",
        "drafting",
        "compliance_review",
        "package_assembly",
        "package_ready",
    ]


def test_gate_event_has_exactly_fifteen_values() -> None:
    values = [e.value for e in enums.GateEvent]
    assert len(values) == 15
    assert values == [
        "documents_uploaded",
        "corpus_ready",
        "g1_approved",
        "g15_submitted",
        "analysis_complete",
        "g2a_confirmed",
        "g25_approved",
        "draft_complete",
        "g3_approved",
        "artifacts_built",
        "registry_bumped",
        "picks_changed",
        "strategy_revised",
        "semantic_finding_regen",
        "new_cycle_started",  # BUS-05: the explicit package_ready -> evidence_review cycle
    ]


@pytest.mark.parametrize(
    ("enum_cls", "expected"),
    [
        (enums.UserRole, {"paralegal", "attorney", "admin"}),
        (enums.GateAction, {"approve", "reject", "edit", "override", "start_cycle"}),
        (enums.TokenKind, {"fact", "amount", "citation", "exhibit"}),
        (enums.TokenStatus, {"verified", "unverified", "disputed"}),
        (enums.TokenSource, {"extractor", "attorney", "rules"}),
        (
            enums.ReconciliationStatus,
            {"llm_only", "table_only", "table_llm_agree", "table_llm_diff"},
        ),
        (enums.MergeBasis, {"deterministic_key", "llm_tiebreak"}),
        (enums.OverlayStatus, {"applied", "parked_orphaned", "conflict"}),
        (enums.ExtractionStatus, {"ok", "partial", "failed"}),
        (
            enums.DocType,
            {
                "medical_record",
                "bill",
                "police_report",
                "wage_doc",
                "photo",
                "insurance_corr",
                "other",
            },
        ),
        (enums.DocStatus, {"uploaded", "classified", "ocr_done", "extracted", "failed"}),
        (enums.DedupStatus, {"unique", "duplicate_of", "partial_overlap"}),
        (enums.TextSource, {"text_layer", "ocr", "none"}),
        (enums.UploadSessionStatus, {"open", "committed", "expired"}),
        (enums.DedupResolution, {"pending", "kept", "superseded"}),
        (
            enums.FlagKind,
            {
                "treatment_gap",
                "preexisting_condition",
                "prior_claim",
                "degenerative_finding",
                "causation_ambiguity",
                "liability_weakness",
                "low_property_damage",
                "third_party_phi",
            },
        ),
        (enums.FlagSeverity, {"low", "medium", "high"}),
        (
            enums.FlagDisposition,
            {"address_in_letter", "omit_with_rationale", "need_more_records"},
        ),
        (enums.FlagDetector, {"date_math", "label", "heuristic_llm"}),
        (enums.PhiDisposition, {"pending", "cleared", "excluded"}),
        (enums.FindingBucket, {"mechanical", "semantic"}),
        (enums.FindingGating, {"blocking", "advisory"}),
        (enums.RunKind, {"phase0", "analysis", "demand"}),
        (
            enums.SseEvent,
            {
                "status",
                "doc_state",
                "section",
                "gate_ready",
                "artifact_ready",
                "budget_warning",
                "error",
            },
        ),
        (enums.ClaimType, {"mva"}),
        (
            enums.LedgerCategory,
            {"er", "ambulance", "imaging", "pt_chiro", "ortho", "surgery", "pharmacy", "other"},
        ),
        (
            enums.ArtifactKind,
            {"letter_docx", "binder_pdf", "chronology_xlsx", "provenance_report"},
        ),
        (enums.DeadlineKind, {"sol", "notice_of_claim"}),
        (enums.RuleVerifyStatus, {"verified", "unverified"}),
    ],
)
def test_enum_value_sets(enum_cls: type[StrEnum], expected: set[str]) -> None:
    assert {m.value for m in enum_cls} == expected


def test_every_domain_enum_is_a_strenum() -> None:
    enum_classes = [
        obj
        for name in dir(enums)
        if isinstance(obj := getattr(enums, name), type)
        and issubclass(obj, StrEnum)
        and obj is not StrEnum
    ]
    # Sanity: we actually discovered the module's enums, not an empty set.
    assert len(enum_classes) >= 20
    for cls in enum_classes:
        assert issubclass(cls, str), f"{cls.__name__} is not a str subclass"
        for member in cls:
            assert isinstance(member, str)
