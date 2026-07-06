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
    """Verification status of a fact token.

    ``DISPUTED`` is a hard block on G3: a token whose value the attorney has contested is
    neither verified nor merely unseen, and a draft may not ship citing it (fact_registry §3).
    """

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"


class TokenSource(StrEnum):
    """Provenance of a fact token — who/what asserted it."""

    EXTRACTOR = "extractor"
    ATTORNEY = "attorney"
    RULES = "rules"


class ReconciliationStatus(StrEnum):
    """How a billing line's numbers were sourced.

    M2 ships only ``LLM_ONLY`` (a single LLM reader over the bill); the table-vs-LLM
    reconciliation pair (``TABLE_ONLY``/``TABLE_LLM_AGREE``/``TABLE_LLM_DIFF``) lands with the
    S1 OCR-vendor decision, when a deterministic table read exists to reconcile against.
    """

    LLM_ONLY = "llm_only"
    TABLE_ONLY = "table_only"
    TABLE_LLM_AGREE = "table_llm_agree"
    TABLE_LLM_DIFF = "table_llm_diff"


class MergeBasis(StrEnum):
    """Why two extracted encounter rows were merged into one (medical_records extractor).

    ``DETERMINISTIC_KEY`` is a same-(date, provider) collision resolved by rule;
    ``LLM_TIEBREAK`` is an ambiguous pair the merge_tiebreak model was asked to adjudicate.
    """

    DETERMINISTIC_KEY = "deterministic_key"
    LLM_TIEBREAK = "llm_tiebreak"


class OverlayStatus(StrEnum):
    """Outcome of reapplying a paralegal's chronology-row overlay after a rebuild.

    A rebuilt chronology row either takes the overlay (``APPLIED``), loses its anchor encounter
    (``PARKED_ORPHANED``), or has drifted under the edit (``CONFLICT``) — never silently
    dropped (chronology_builder §3).
    """

    APPLIED = "applied"
    PARKED_ORPHANED = "parked_orphaned"
    CONFLICT = "conflict"


class ExtractionStatus(StrEnum):
    """Per-window ``ExtractionRun`` status (corpus_extraction §4)."""

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


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
    """Where a page's text came from.

    ``NONE`` is an image-only page with no text layer and no OCR result yet (a
    ``zero_text`` page): OCR was skipped or has not run, so there is no text version to
    attribute a source to.
    """

    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    NONE = "none"


class UploadSessionStatus(StrEnum):
    """Lifecycle of a resumable batch-upload session.

    (An async-commit ``completing`` state from the design doc is deliberately omitted: M1
    commit is synchronous. The Wave C contract doc records the omission.)
    """

    OPEN = "open"
    COMMITTED = "committed"
    EXPIRED = "expired"


class DedupResolution(StrEnum):
    """Human resolution of a quarantined dedup decision — never auto-resolved.

    A decision starts ``PENDING``; an attorney resolves it to ``KEPT`` (the new doc stands)
    or ``SUPERSEDED`` (the new doc is a duplicate/subset and drops out of the ledger).
    """

    PENDING = "pending"
    KEPT = "kept"
    SUPERSEDED = "superseded"


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


class FlagDetector(StrEnum):
    """Provenance of a risk flag — how it was produced (risk_flag_engine §3).

    ``DATE_MATH`` is a deterministic date-arithmetic detector (e.g. the treatment-gap gap
    check); ``LABEL`` is the LLM labeling pass over the record; ``HEURISTIC_LLM`` is a
    rule-plus-LLM heuristic (e.g. low-property-damage cross-checked against injury treatment).
    """

    DATE_MATH = "date_math"
    LABEL = "label"
    HEURISTIC_LLM = "heuristic_llm"


class PhiDisposition(StrEnum):
    """Third-party-PHI disposition on an exhibit (package_builder §3).

    ``PENDING`` (the default) blocks the M5 binder build until a human decides: a page with
    someone else's PHI is neither cleared for inclusion (``CLEARED``) nor dropped from the
    exhibit (``EXCLUDED``) until reviewed.
    """

    PENDING = "pending"
    CLEARED = "cleared"
    EXCLUDED = "excluded"


class FindingBucket(StrEnum):
    """Compliance-finding bucket (G3 panel)."""

    MECHANICAL = "mechanical"
    SEMANTIC = "semantic"


class FindingGating(StrEnum):
    """Whether a compliance finding blocks G3 or is advisory.

    Also the vocabulary of ``ComplianceFinding.severity`` (compliance §Vocabulary: ``severity`` ∈
    {``blocking``, ``advisory``}) — the ORM column is named ``severity`` per the contract, but its
    values are this enum's members.
    """

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class SectionValidation(StrEnum):
    """Deterministic validation state of a ``DraftSection`` (brain2 §Vocabulary).

    A section either passes deterministic validation (``PASSED``), is mid-retry after a first
    failure (``RETRY_PENDING`` — the default at mint), or has failed validation twice and
    **surfaced** rather than looping to satisfy a proxy (``SURFACED_FAILED``; brain2 inv 1/5).
    """

    PASSED = "passed"
    RETRY_PENDING = "retry_pending"
    SURFACED_FAILED = "surfaced_failed"


class FindingStatus(StrEnum):
    """The G3 compliance-finding lifecycle (compliance §Vocabulary).

    A finding opens (``OPEN``), is fixed by a mechanical span-patch (``PATCHED``) or a
    single-section regen (``REGENERATED``), is then **always** re-verified (``RE_VERIFIED`` — the
    mandatory re-verify-after-fix step that catches a fix introducing a new orphan), and finally
    is dispositioned by the attorney (``DISPOSITIONED``). Re-verify ALWAYS follows a patch/regen.
    """

    OPEN = "open"
    PATCHED = "patched"
    REGENERATED = "regenerated"
    RE_VERIFIED = "re_verified"
    DISPOSITIONED = "dispositioned"


class FindingDisposition(StrEnum):
    """Attorney disposition of a compliance finding at G3.

    ``ACCEPT`` takes the finding's fix as-is; ``OVERRIDE`` proceeds past an advisory finding with
    a recorded reason. Hard-block check kinds (compliance §Vocabulary) are never overridable to
    ship — the disposition set does not let a blocking orphan/AMT-mismatch/dead-anchor through.
    """

    ACCEPT = "accept"
    OVERRIDE = "override"


class CheckKind(StrEnum):
    """The G3 compliance-check taxonomy (compliance §Responsibility / §Vocabulary).

    The first SEVEN are **deterministic / mechanical-eligible** (pure-code predicates —
    span-patch-routable for the enumerated set): ``ORPHAN_TOKEN``, ``AMT_LEDGER_MISMATCH``,
    ``DEAD_ANCHOR``, ``MISSING_EXHIBIT``, ``MISSING_STATUTORY_TERM``, ``UNDISPOSED_ADVERSE``,
    ``PROSE_TOTAL_MISMATCH``. The last THREE are **semantic** (the Sonnet judge, never a
    code-side normalizer): ``UNSUPPORTED_CAUSATION``, ``STRATEGY_DRIFT``, ``TONE``. The hard
    blocks (never overridable to ship) are ``orphan_token``, ``amt_ledger_mismatch``,
    ``dead_anchor``, ``missing_exhibit``, ``undisposed_adverse`` (+ a registry-version mismatch),
    per the compliance contract.
    """

    ORPHAN_TOKEN = "orphan_token"
    AMT_LEDGER_MISMATCH = "amt_ledger_mismatch"
    DEAD_ANCHOR = "dead_anchor"
    MISSING_EXHIBIT = "missing_exhibit"
    MISSING_STATUTORY_TERM = "missing_statutory_term"
    UNDISPOSED_ADVERSE = "undisposed_adverse"
    PROSE_TOTAL_MISMATCH = "prose_total_mismatch"
    UNSUPPORTED_CAUSATION = "unsupported_causation"
    STRATEGY_DRIFT = "strategy_drift"
    TONE = "tone"


class DraftStatus(StrEnum):
    """Lifecycle of a ``DemandDraft`` (brain2 → compliance → package).

    A draft is ``DRAFTING`` while Brain-2 emits sections, ``VALIDATED`` once every section passes
    deterministic validation, ``IN_COMPLIANCE`` while the G3 panel runs, ``APPROVED`` at G3
    approve (zero open blocking findings), and ``SUPERSEDED`` when a newer draft version replaces
    it (a re-draft after drift is a new version, never an overwrite).
    """

    DRAFTING = "drafting"
    VALIDATED = "validated"
    IN_COMPLIANCE = "in_compliance"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


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
