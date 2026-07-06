"""Domain enums — the single home for every ClarionPI domain enum.

All enums are ``StrEnum`` so their members are plain strings on the wire and in the
database (columns are stored as ``String`` and validated at the Pydantic layer — see
``orm.py`` for why we avoid ``sa.Enum``). Member *values* are the canonical serialized
form; keep them stable, they are contract.
"""

from __future__ import annotations

from enum import StrEnum


class GateState(StrEnum):
    """The ten states of the matter gate machine (04 §2 / orchestrator_gates)."""

    CORPUS_PROCESSING = "corpus_processing"
    FACTS_REVIEW = "facts_review"
    STRATEGY_INTAKE = "strategy_intake"
    ANALYSIS_RUNNING = "analysis_running"
    EVIDENCE_REVIEW = "evidence_review"
    PLAN_REVIEW = "plan_review"
    DRAFTING = "drafting"
    COMPLIANCE_REVIEW = "compliance_review"
    PACKAGE_ASSEMBLY = "package_assembly"
    PACKAGE_READY = "package_ready"


class GateEvent(StrEnum):
    """Events that drive gate transitions (orchestrator_gates transition table)."""

    DOCUMENTS_UPLOADED = "documents_uploaded"
    CORPUS_READY = "corpus_ready"
    G1_APPROVED = "g1_approved"
    G15_SUBMITTED = "g15_submitted"
    ANALYSIS_COMPLETE = "analysis_complete"
    G2A_CONFIRMED = "g2a_confirmed"
    G25_APPROVED = "g25_approved"
    DRAFT_COMPLETE = "draft_complete"
    G3_APPROVED = "g3_approved"
    ARTIFACTS_BUILT = "artifacts_built"
    REGISTRY_BUMPED = "registry_bumped"
    PICKS_CHANGED = "picks_changed"
    STRATEGY_REVISED = "strategy_revised"
    SEMANTIC_FINDING_REGEN = "semantic_finding_regen"


class UserRole(StrEnum):
    """User roles; drives server-side gate role guards (invariant 8)."""

    PARALEGAL = "paralegal"
    ATTORNEY = "attorney"
    ADMIN = "admin"


class GateAction(StrEnum):
    """The action recorded on a ``GateRecord`` for a transition."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    OVERRIDE = "override"


class TokenKind(StrEnum):
    """Fact-registry token kinds; one per-matter namespace (fact_registry)."""

    FACT = "fact"
    AMOUNT = "amount"
    CITATION = "citation"
    EXHIBIT = "exhibit"


class TokenStatus(StrEnum):
    """Verification status of a fact token."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class TokenSource(StrEnum):
    """Provenance of a fact token — who/what asserted it."""

    EXTRACTOR = "extractor"
    ATTORNEY = "attorney"
    RULES = "rules"


class DocType(StrEnum):
    """Classification of an uploaded case document."""

    MEDICAL_RECORD = "medical_record"
    BILL = "bill"
    POLICE_REPORT = "police_report"
    WAGE_DOC = "wage_doc"
    PHOTO = "photo"
    INSURANCE_CORR = "insurance_corr"
    OTHER = "other"


class DocStatus(StrEnum):
    """Phase-0 processing status of a case document."""

    UPLOADED = "uploaded"
    CLASSIFIED = "classified"
    OCR_DONE = "ocr_done"
    EXTRACTED = "extracted"
    FAILED = "failed"


class DedupStatus(StrEnum):
    """Dedup resolution for a document (drives ledger inclusion in money_engine)."""

    UNIQUE = "unique"
    DUPLICATE_OF = "duplicate_of"
    PARTIAL_OVERLAP = "partial_overlap"


class TextSource(StrEnum):
    """Where a page's text came from."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"


class FlagKind(StrEnum):
    """Risk-flag taxonomy (risk_flag_engine, 01 §7)."""

    TREATMENT_GAP = "treatment_gap"
    PREEXISTING_CONDITION = "preexisting_condition"
    PRIOR_CLAIM = "prior_claim"
    DEGENERATIVE_FINDING = "degenerative_finding"
    CAUSATION_AMBIGUITY = "causation_ambiguity"
    LIABILITY_WEAKNESS = "liability_weakness"
    LOW_PROPERTY_DAMAGE = "low_property_damage"
    THIRD_PARTY_PHI = "third_party_phi"


class FlagSeverity(StrEnum):
    """Severity band for a risk flag; high-severity gates G2a confirm."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FlagDisposition(StrEnum):
    """Attorney disposition of a risk flag at G2a."""

    ADDRESS_IN_LETTER = "address_in_letter"
    OMIT_WITH_RATIONALE = "omit_with_rationale"
    NEED_MORE_RECORDS = "need_more_records"


class FindingBucket(StrEnum):
    """Compliance-finding bucket (G3 panel)."""

    MECHANICAL = "mechanical"
    SEMANTIC = "semantic"


class FindingGating(StrEnum):
    """Whether a compliance finding blocks G3 or is advisory."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class RunKind(StrEnum):
    """Background run kinds coordinated by the orchestrator."""

    PHASE0 = "phase0"
    ANALYSIS = "analysis"
    DEMAND = "demand"


class SseEvent(StrEnum):
    """SSE event vocabulary (04 §4). No internal-reasoning events by design."""

    STATUS = "status"
    DOC_STATE = "doc_state"
    SECTION = "section"
    GATE_READY = "gate_ready"
    ARTIFACT_READY = "artifact_ready"
    BUDGET_WARNING = "budget_warning"
    ERROR = "error"


class ClaimType(StrEnum):
    """Supported claim types. MVP: motor vehicle accident only."""

    MVA = "mva"


class LedgerCategory(StrEnum):
    """Fixed v1 specials-ledger category taxonomy (money_engine)."""

    ER = "er"
    AMBULANCE = "ambulance"
    IMAGING = "imaging"
    PT_CHIRO = "pt_chiro"
    ORTHO = "ortho"
    SURGERY = "surgery"
    PHARMACY = "pharmacy"
    OTHER = "other"


class ArtifactKind(StrEnum):
    """Deliverable artifact kinds produced at package assembly."""

    LETTER_DOCX = "letter_docx"
    BINDER_PDF = "binder_pdf"
    CHRONOLOGY_XLSX = "chronology_xlsx"
    PROVENANCE_REPORT = "provenance_report"


class DeadlineKind(StrEnum):
    """Rules-computed deadline candidate kinds."""

    SOL = "sol"
    NOTICE_OF_CLAIM = "notice_of_claim"


class RuleVerifyStatus(StrEnum):
    """Verification status of a rules-derived deadline candidate."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
