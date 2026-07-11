/**
 * Wire types — hand-mirrored from the backend view-models. These are the ONLY shapes the
 * frontend consumes; when a backend view-model changes, update the mirror here.
 *
 * Sources (backend, read at M3-C build time):
 *   - MatterView / DeadlineCandidate / *View → backend/app/api/view_models.py
 *   - DeadlineCandidate fields               → backend/app/models/schemas.py
 *   - enum string values                     → backend/app/models/enums.py
 *
 * NOTE ON NAMING: the backend uses domain field names that differ from an intuitive guess.
 * The mirror matches the backend exactly (a mismatch is silent data loss), so:
 *   - MatterView carries `deadline_candidates` (NOT `sol_candidates`), and does NOT expose
 *     `venue_county` (stored on the ORM, but not projected onto the view).
 *   - DeadlineCandidate fields are `kind` / `date` / `statute_cite` / `verify_status` /
 *     `confirmed` (NOT rule_id / deadline_kind / computed_date). `verify_status` and
 *     `confirmed` are REQUIRED on the wire (matter_to_view validates them), so they are not
 *     optional here. There is no `diagnostic` field on this view-model.
 */

// ---------------------------------------------------------------------------------------
// Enum string-literal unions (mirror StrEnum values in backend/app/models/enums.py)
// ---------------------------------------------------------------------------------------

/** The ten states of the matter gate machine — GateState in enums.py, in order. */
export type GateState =
  | "corpus_processing"
  | "facts_review"
  | "strategy_intake"
  | "analysis_running"
  | "evidence_review"
  | "plan_review"
  | "drafting"
  | "compliance_review"
  | "package_assembly"
  | "package_ready";

/** Ordered gate states — drives the stepper. Index === progress. */
export const GATE_STATES: readonly GateState[] = [
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
] as const;

/** Human labels for the stepper — display only, never sent back. */
export const GATE_STATE_LABELS: Record<GateState, string> = {
  corpus_processing: "Corpus processing",
  facts_review: "Facts review (G1)",
  strategy_intake: "Strategy intake (G1.5)",
  analysis_running: "Analysis running",
  evidence_review: "Evidence review (G2a)",
  plan_review: "Plan review (G2.5)",
  drafting: "Drafting",
  compliance_review: "Compliance review (G3)",
  package_assembly: "Package assembly",
  package_ready: "Package ready",
};

/** ClaimType — MVP is motor-vehicle-accident only. */
export type ClaimType = "mva";

/** DeadlineKind. */
export type DeadlineKind = "sol" | "notice_of_claim";

/** RuleVerifyStatus for a deadline candidate. */
export type RuleVerifyStatus = "verified" | "unverified";

/** DocType classification. */
export type DocType =
  | "medical_record"
  | "bill"
  | "police_report"
  | "wage_doc"
  | "photo"
  | "insurance_corr"
  | "other";

/** DocStatus — Phase-0 processing status of a case document. */
export type DocStatus =
  | "uploaded"
  | "classified"
  | "ocr_done"
  | "extracted"
  | "failed";

/** DedupStatus. */
export type DedupStatus = "unique" | "duplicate_of" | "partial_overlap";

/** DedupResolution — the human resolution vocabulary; `pending` is unresolved. */
export type DedupResolution = "pending" | "kept" | "superseded";

/** UploadSessionStatus. */
export type UploadSessionStatus = "open" | "committed" | "expired";

/** TextSource for a page. */
export type TextSource = "text_layer" | "ocr" | "none";

/** UserRole — drives the role chip and server-side gate guards. */
export type UserRole = "paralegal" | "attorney" | "admin";

// ---------------------------------------------------------------------------------------
// G2a (evidence_review) enum unions — mirror StrEnum values in enums.py, read at M4-D build.
// ---------------------------------------------------------------------------------------

/** OverlayStatus — how a rebuilt chronology row's overlay reapplied (`null` when untouched). */
export type OverlayStatus = "applied" | "parked_orphaned" | "conflict";

/** FlagKind — the risk-flag taxonomy. */
export type FlagKind =
  | "treatment_gap"
  | "preexisting_condition"
  | "prior_claim"
  | "degenerative_finding"
  | "causation_ambiguity"
  | "liability_weakness"
  | "low_property_damage"
  | "third_party_phi";

/** FlagSeverity — high gates the G2a confirm. */
export type FlagSeverity = "low" | "medium" | "high";

/** FlagDetector — provenance of a risk flag (deterministic math vs LLM label vs heuristic). */
export type FlagDetector = "date_math" | "label" | "heuristic_llm";

/** FlagDisposition — the closed set of attorney dispositions on a risk flag at G2a. */
export type FlagDisposition =
  | "address_in_letter"
  | "omit_with_rationale"
  | "need_more_records";

/** PhiDisposition — third-party-PHI disposition on an exhibit (`pending` blocks the binder). */
export type PhiDisposition = "pending" | "cleared" | "excluded";

/** LedgerCategory — the fixed v1 specials-ledger category taxonomy. */
export type LedgerCategory =
  | "er"
  | "ambulance"
  | "imaging"
  | "pt_chiro"
  | "ortho"
  | "surgery"
  | "pharmacy"
  | "other";

// ---------------------------------------------------------------------------------------
// View-models (mirror backend/app/api/view_models.py)
// ---------------------------------------------------------------------------------------

/**
 * DeadlineCandidate — a rules-computed deadline, attorney-confirmed at G1.
 * Mirror of schemas.py::DeadlineCandidate. `verify_status` and `confirmed` are required.
 */
export interface DeadlineCandidate {
  kind: DeadlineKind;
  /** ISO date string (YYYY-MM-DD). */
  date: string;
  statute_cite: string;
  assumptions: string[];
  verify_status: RuleVerifyStatus;
  confirmed: boolean;
}

/**
 * IntakeFlagAnswer — tri-state pilot-intake answer (WI-2). Creation accepts a matter only
 * when every flag is "no"; "unknown" marks a matter that predates the preflight.
 */
export type IntakeFlagAnswer = "yes" | "no" | "unknown";

/** The four pilot-intake eligibility flags, in canonical (request/display) order. */
export const INTAKE_FLAG_KEYS = [
  "public_entity_involved",
  "plaintiff_is_minor",
  "wrongful_death",
  "coverage_dispute",
] as const;

export type IntakeFlagKey = (typeof INTAKE_FLAG_KEYS)[number];

/** One per-flag reason in a `matter_out_of_scope` 422 refusal body. */
export interface IntakeScopeReason {
  flag: IntakeFlagKey;
  answer: IntakeFlagAnswer;
  reason: string;
}

/** MatterView — returned by POST /api/matters and GET /api/matters/{id}. */
export interface MatterView {
  id: string;
  client_display_name: string;
  claim_type: ClaimType;
  jurisdiction: string;
  /** ISO date string (YYYY-MM-DD). */
  incident_date: string;
  gate_state: GateState;
  registry_version: number;
  deadline_candidates: DeadlineCandidate[];
  public_entity_involved: IntakeFlagAnswer;
  plaintiff_is_minor: IntakeFlagAnswer;
  wrongful_death: IntakeFlagAnswer;
  coverage_dispute: IntakeFlagAnswer;
}

/** UploadSlotView — one file slot; `upload_url` is where the client PUTs the bytes. */
export interface UploadSlotView {
  id: string;
  /** Zero-based registration order — the stable pairing key (never pair by array index). */
  ordinal: number;
  filename: string;
  size_bytes: number;
  received: boolean;
  upload_url: string | null;
}

/** UploadSessionView — a registered batch and its slots. */
export interface UploadSessionView {
  id: string;
  matter_id: string;
  status: UploadSessionStatus;
  /** ISO datetime string. */
  ttl_expires_at: string;
  slots: UploadSlotView[];
}

/** DocumentView — a case document (post-classification). */
export interface DocumentView {
  id: string;
  matter_id: string;
  doc_type: DocType;
  status: DocStatus;
  dedup_status: DedupStatus;
  filename: string;
  page_count: number;
  needs_review: boolean;
  classification_confidence: number | null;
  failure_reason: string | null;
}

/** PageView — a single document page (the browsable page store). */
export interface PageView {
  id: string;
  document_id: string;
  page_no: number;
  text: string;
  text_source: TextSource;
  ocr_confidence: number | null;
  zero_text: boolean;
  image_ref: string | null;
}

/** DedupDecisionView — a quarantined dedup decision awaiting human resolution. */
export interface DedupDecisionView {
  id: string;
  matter_id: string;
  document_id: string;
  against_document_id: string | null;
  status: DedupStatus;
  page_hash_matches: unknown[];
  shingle_overlap: number | null;
  resolution: DedupResolution;
}

/**
 * UserView — the authenticated user chip. The auth wave (parallel) will return this from
 * GET /api/auth/me; until it lands, auth.me() resolves null (logged-out). Fields mirror the
 * seeded User (email, display_name, role) plus id.
 */
export interface UserView {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
}

// ---------------------------------------------------------------------------------------
// Envelope shapes for list endpoints (the backend wraps lists in a keyed object)
// ---------------------------------------------------------------------------------------

/** GET /api/matters/{id}/documents → { documents: [...] }. */
export interface DocumentListResponse {
  documents: DocumentView[];
}

/** GET /api/matters/{id}/dedup → { decisions: [...] }. */
export interface DedupListResponse {
  decisions: DedupDecisionView[];
}

/** POST /api/uploads/{id}/commit → { session_id, documents: [...] }. */
export interface CommitResponse {
  session_id: string;
  documents: DocumentView[];
}

// ---------------------------------------------------------------------------------------
// Gate envelope + per-gate view-models (mirror backend/app/api/routes/gates.py +
// view_models.py::{facts_review_vm, strategy_intake_vm, minimal_gate_vm}). Read at M3-D
// build time. The envelope is heterogeneous per gate: `view_model` is a union keyed by
// which gate `gate` names. The gates route scans the whole envelope through the wire
// token-scanner before it leaves, so nothing token-shaped ever arrives here.
// ---------------------------------------------------------------------------------------

/** GateAction — the closed set of gate submit actions (mirror GateAction in enums.py). */
export type GateAction = "approve" | "reject" | "edit" | "override" | "start_cycle";

/**
 * DeadlineCandidateVM — the deadline candidate as the GATES view-model projects it. This
 * differs from the matter-level {@link DeadlineCandidate}: `facts_review_vm` adds a
 * `rule_id` (the candidate's `statute_cite`, the identifier the FE echoes back in a
 * DeadlineConfirmation). Everything else mirrors {@link DeadlineCandidate}.
 */
export interface DeadlineCandidateVM {
  kind: DeadlineKind;
  /** ISO date string (YYYY-MM-DD). */
  date: string;
  statute_cite: string;
  assumptions: string[];
  verify_status: RuleVerifyStatus;
  confirmed: boolean;
  /** === statute_cite; the stable id echoed in a DeadlineConfirmation submit. */
  rule_id: string;
}

/** The G1 (facts_review) view-model. `incident_facts` is null until an IncidentFacts row exists. */
export interface FactsVM {
  deadline_candidates: DeadlineCandidateVM[];
  incident_facts: {
    payload: Record<string, unknown>;
    anchors: unknown[];
  } | null;
  documents_summary: {
    total: number;
    needs_review: number;
    failed: number;
  };
}

/** The G1.5 (strategy_intake) `strategy_inputs` block — money fields are integer cents. */
export interface StrategyInputsVM {
  liability_theory: string;
  injury_framing: string;
  emphasis_notes: string;
  venue_posture: string;
  anchor_amount_cents: number | null;
  /** ISO date string (YYYY-MM-DD) or null. */
  mmi_date: string | null;
  property_damage_estimate_cents: number | null;
}

/** The G1.5 (strategy_intake) view-model. */
export interface StrategyIntakeVM {
  strategy_inputs: StrategyInputsVM;
  deadlines_confirmed: boolean;
}

// ---------------------------------------------------------------------------------------
// G2a (evidence_review) view-model — mirror view_models.py::evidence_review_vm. Every money
// value is integer cents (the FE renders via centsToDollars; it NEVER computes a total).
// Nothing token-shaped arrives: `exhibit_token_id` is the BARE id ("EX_1"), chronology
// narratives are already resolved to display forms.
// ---------------------------------------------------------------------------------------

/** One chronology row, wire-rendered (narrative already token-resolved). `overlay_status` null = untouched. */
export interface ChronologyRow {
  row_id: string;
  /** ISO date string (YYYY-MM-DD). */
  date_of_service: string;
  provider_display: string;
  facility_display: string;
  encounter_type: string;
  narrative: string;
  anchors: unknown[];
  overlay_status: OverlayStatus | null;
}

/** The chronology block: derived rows + the overlay-quarantine counts. */
export interface ChronologyVM {
  rows: ChronologyRow[];
  conflicts: number;
  parked: number;
}

/** One ledger column-set — integer cents only (billed / adjusted / paid / outstanding). */
export interface LedgerColumns {
  billed_cents: number;
  adjusted_cents: number;
  paid_cents: number;
  outstanding_cents: number;
}

/**
 * The specials ledger — category rows + a grand total, plus the demand-basis and the two
 * visibility lists (a gap is shown, never swallowed). Money is cents; the FE renders, never sums.
 */
export interface LedgerVM {
  by_category: Record<string, LedgerColumns>;
  grand_total: LedgerColumns;
  demand_basis_total_cents: number;
  basis: string;
  line_set_hash: string;
  missing_paid_line_ids: string[];
  excluded_line_ids: string[];
}

/** One risk flag as the G2a wire projects it (incl. detector + disposition_role for the audit view). */
export interface RiskFlagVM {
  id: string;
  kind: FlagKind | string;
  severity: FlagSeverity;
  detail: string;
  anchors: unknown[];
  disposition: FlagDisposition | null;
  disposition_role: UserRole | null;
  detector: FlagDetector | string;
  disposition_rationale?: string | null;
}

/** One draft-binder exhibit entry (bare `exhibit_token_id`; `null` until minted). */
export interface ExhibitEntry {
  exhibit_token_id: string | null;
  document_id: string;
  filename: string;
  included_pages: number[];
  excluded_pages: number[];
  phi_disposition: PhiDisposition;
  sort_order: number;
  page_count: number;
  integrity: string;
}

/** The exhibits block: the manifest entries + the M5-binder `blocking` reasons. */
export interface ExhibitsVM {
  entries: ExhibitEntry[];
  blocking: string[];
}

/** The G2a (evidence_review) view-model — chronology + ledger + risk flags + exhibits. */
export interface EvidenceReviewVM {
  chronology: ChronologyVM;
  /** `null` when the jurisdiction pack is unsupported (defensive; creation already gates it). */
  ledger: LedgerVM | null;
  risk_flags: RiskFlagVM[];
  exhibits: ExhibitsVM;
  dedup_pending: number;
}

/** The honest placeholder VM for gates whose UI lands in a later milestone (minimal_gate_vm). */
export interface MinimalGateVM {
  state: string;
  detail: string;
}

/** One failing approve guard, from `role_affordances.approve_blockers` (dry-run, no side effects). */
export interface ApproveBlocker {
  guard: string;
  code: string;
  detail: string;
}

/** Role affordances for the current actor at the current gate — advisory; the server is authority. */
export interface RoleAffordances {
  can_edit: boolean;
  can_approve: boolean;
  approve_blockers: ApproveBlocker[];
}

/**
 * The gates envelope — GET /api/matters/{id}/gates/current. `view_model` is one of the
 * per-gate shapes above (discriminated by `gate` at the call site). `payload_version` is
 * the optimistic fence the FE must echo on the next submit.
 */
export interface GateEnvelope {
  gate: GateState;
  payload_version: number;
  view_model:
    | FactsVM
    | StrategyIntakeVM
    | EvidenceReviewVM
    | PlanReviewVM
    | ComplianceReviewVM
    | PackageVM
    | MinimalGateVM;
  role_affordances: RoleAffordances;
}

/** G1 edit payload — per-candidate confirmations + optional str→str intake-fact merge. */
export interface FactsReviewEdits {
  deadline_confirmations: { rule_id: string; confirmed: boolean }[];
  incident_facts?: Record<string, string>;
}

/**
 * G1.5 edit payload — the seven nullable StrategyInputs fields, upserted VERBATIM. Only
 * fields present (non-undefined) are sent; a `null` clears the stored value.
 */
export interface StrategyIntakeEdits {
  liability_theory?: string;
  injury_framing?: string;
  emphasis_notes?: string;
  venue_posture?: string;
  anchor_amount_cents?: number | null;
  mmi_date?: string | null;
  property_damage_estimate_cents?: number | null;
}

/**
 * The gate submit body (POST .../gates/{gate}/submit). Closed: the backend GateSubmit is
 * `extra="forbid"`, and this mirror carries ONLY these keys — no overlay / view-model echo
 * (invariant: submissions carry no view-model fields).
 */
export interface GateSubmitBody {
  action: GateAction;
  /** Client-minted, 8..64 chars (crypto.randomUUID sliced to 36). */
  idempotency_key: string;
  payload_version: number;
  override_reason?: string;
  edits?: FactsReviewEdits | StrategyIntakeEdits | PlanReviewEdits;
}

/** The gate submit success body (200). */
export interface GateSubmitResult {
  result: {
    transitioned: boolean;
    from_state: string;
    to_state: string;
    replayed: boolean;
  };
  matter: MatterView;
  record_id: string;
}

/** GET /api/matters → { matters: [...] }. */
export interface MatterListResponse {
  matters: MatterView[];
}

/**
 * UserView extended with `auth_mode` — GET /api/auth/me returns the user fields plus the
 * active auth mode at the TOP level (not nested). Login (POST /api/auth/login) returns
 * `{ user: UserView }` (no auth_mode) — see {@link LoginResponse}.
 */
export interface MeView extends UserView {
  auth_mode: string;
}

/** POST /api/auth/login success body — the user is nested under `user`. */
export interface LoginResponse {
  user: UserView;
}

// ---------------------------------------------------------------------------------------
// G2a (evidence_review) action bodies + response envelopes (mirror routes/evidence.py,
// routes/analysis.py, and the two PINNED sibling contracts). Money crosses the wire as
// dollar STRINGS on the way IN (empty string clears); cents on the way back out.
// ---------------------------------------------------------------------------------------

/** PUT /api/flags/{flag_id}/disposition — rationale REQUIRED (non-blank) for omit_with_rationale. */
export interface FlagDispositionBody {
  disposition: FlagDisposition;
  rationale?: string;
}

/** PUT /api/matters/{id}/exhibits — a per-document exhibit pick (1-based page lists + order). */
export interface ExhibitPickBody {
  document_id: string;
  include_pages: number[];
  excluded_pages: number[];
  sort_order: number;
}

/** POST /api/exhibits/{id}/phi — only cleared / excluded travel this path (pending is not a target). */
export interface PhiDispositionBody {
  disposition: Exclude<PhiDisposition, "pending">;
}

/** GET /api/matters/{id}/manifest?mint=true → the binder manifest (entries + blocking). */
export interface ManifestResponse {
  matter_id: string;
  entries: ExhibitEntry[];
  blocking: string[];
}

/**
 * One source-row billing edit. Money fields are dollar STRINGS as-typed (parsed to cents at the
 * service); an empty string CLEARS that field; a field left `undefined` is untouched.
 */
export interface BillingLineEdit {
  billing_line_id: string;
  category?: LedgerCategory;
  billed?: string;
  adjusted?: string;
  paid?: string;
  outstanding?: string;
}

/** POST /api/matters/{id}/billing/edits body — a non-empty batch of source-row edits. */
export interface BillingEditBatch {
  edits: BillingLineEdit[];
}

/**
 * POST /api/matters/{id}/billing/edits success — the edit counts + the recomputed ledger. The FE
 * REPLACES its ledger display from `ledger` (server-authoritative; the FE never sums).
 */
export interface BillingEditResponse {
  outcome: {
    edited: number;
    recategorized: number;
    reparsed_money_fields: number;
  };
  ledger: LedgerVM;
}

/** One source billing line — GET /api/matters/{id}/billing/lines (PINNED sibling contract). */
export interface BillingLine {
  id: string;
  provider: string;
  /** ISO date string (YYYY-MM-DD). */
  date_of_service: string;
  code: string | null;
  billed_cents: number;
  adjusted_cents: number | null;
  paid_cents: number | null;
  outstanding_cents: number | null;
  category: string;
  document_id: string | null;
}

/** GET /api/matters/{id}/billing/lines → { lines: [...] } (PINNED sibling contract). */
export interface BillingLinesResponse {
  lines: BillingLine[];
}

/**
 * PUT /api/matters/{id}/chronology/{encounter_id}/overlay body (PINNED sibling contract). The
 * `edited_fields` object is a CLOSED vocabulary — exactly these four optional keys, nothing else.
 */
export interface ChronologyOverlayBody {
  edited_fields: {
    narrative_override?: string;
    provider_display?: string;
    facility_display?: string;
    encounter_type?: string;
  };
}

/** The inline exhibit view returned by the pick + PHI endpoints (plain scalars, no tokens). */
export interface ExhibitView {
  id: string;
  document_id: string;
  include_pages: number[];
  excluded_pages: number[];
  phi_disposition: PhiDisposition;
  sort_order: number;
}

// ---------------------------------------------------------------------------------------
// M5 (plan_review / drafting / compliance_review / package_*) — hand-mirrored from the
// backend view-models read at M5-Wave-D2 build time:
//   - plan_review_vm / compliance_review_vm / package_vm  → backend/app/api/view_models.py
//   - PlanView (StrategyPlan) / PlannedSection            → backend/app/models/schemas.py
//   - ComplianceFindingView / RenderedSpan / SpanRef      → backend/app/models/schemas.py
//   - enum string values (CheckKind / FindingBucket / …)  → backend/app/models/enums.py
//   - SSE frame shapes + finding-action refusals          → backend/app/engine/brain2/generate.py,
//     backend/app/api/routes/drafting.py
// Every money value is integer cents (the FE renders via centsToDollars — it NEVER sums).
// Nothing token-shaped arrives: section previews are RENDERED (tokens resolved); a span's
// `token_id` is the BARE registry id ("FACT_3") and is display-inert until M6 wires click-through.
// ---------------------------------------------------------------------------------------

/** DemandType — the closed v1 set: only "open" (a `time_limited` demand is a later version). */
export type DemandType = "open";

/** CheckKind — the G3 compliance-check taxonomy (mirror CheckKind in enums.py). */
export type CheckKind =
  | "orphan_token"
  | "amt_ledger_mismatch"
  | "dead_anchor"
  | "missing_exhibit"
  | "missing_statutory_term"
  | "undisposed_adverse"
  | "prose_total_mismatch"
  | "unsupported_causation"
  | "strategy_drift"
  | "tone";

/**
 * The five HARD-BLOCK check kinds — never overridable to ship; they must be fixed at the
 * underlying data (compliance §Vocabulary). The panel shows an explanatory chip for these.
 */
export const HARD_BLOCK_CHECK_KINDS: ReadonlySet<CheckKind> = new Set<CheckKind>([
  "orphan_token",
  "amt_ledger_mismatch",
  "dead_anchor",
  "missing_exhibit",
  "undisposed_adverse",
]);

/** FindingBucket — mechanical (span-patch-routable) vs semantic (the Sonnet judge). */
export type FindingBucket = "mechanical" | "semantic";

/** FindingSeverity (ORM column `severity`, enum FindingGating) — blocking gates G3; advisory does not. */
export type FindingSeverity = "blocking" | "advisory";

/** FindingStatus — the G3 finding lifecycle (mirror FindingStatus in enums.py). */
export type FindingStatus =
  | "open"
  | "patched"
  | "regenerated"
  | "re_verified"
  | "dispositioned";

/** FindingDisposition — the attorney's reasoned disposition (accept the fix / override past advisory). */
export type FindingDisposition = "accept" | "override";

/** SectionValidation — deterministic validation state of a draft section. */
export type SectionValidation = "passed" | "retry_pending" | "surfaced_failed";

/** One planned demand section — the token budget for a section of the letter (bare token ids). */
export interface PlannedSectionView {
  section_id: string;
  purpose: string;
  allowed_tokens: string[];
  required_tokens: string[];
  max_words: number;
}

/** PlanView — the latest StrategyPlan projected onto the wire (the G2.5 drafting contract). */
export interface PlanView {
  id: string;
  matter_id: string;
  version: number;
  registry_version: number;
  demand_amount_cents: number | null;
  demand_type: DemandType;
  sections: PlannedSectionView[];
  emphasis_directives: string[];
  /** BUS-05: non-null when a registry bump invalidated this plan's approval. */
  invalidated_by_registry_version?: number | null;
  approved: boolean;
  approved_by?: string | null;
  approved_at?: string | null;
}

/** The G2.5 (plan_review) view-model — the latest plan (or null) + a build-plan affordance. */
export interface PlanReviewVM {
  plan: PlanView | null;
  plan_missing: boolean;
  /** The matter's current registry version — compared to plan.registry_version to surface drift. */
  registry_version_current: number;
}

/**
 * One G2.5 per-section plan edit — a partial override of a planned section. `section_id` names
 * an EXISTING planned section (an unknown id is a typed 422 `unknown_plan_section`); every other
 * field is applied only when present. Token lists carry BARE ids (never the bracketed shape).
 */
export interface PlannedSectionEdit {
  section_id: string;
  max_words?: number;
  allowed_tokens?: string[];
  required_tokens?: string[];
}

/**
 * G2.5 (plan_review) edit payload — a partial override that RE-EMITS a new (unapproved) plan
 * version. Only fields present (non-undefined) are sent. `demand_type` is the closed "open" set.
 */
export interface PlanReviewEdits {
  demand_amount_cents?: number | null;
  demand_type?: DemandType;
  emphasis_directives?: string[];
  sections?: PlannedSectionEdit[];
}

/** POST /api/matters/{id}/plan/emit → 200 `{ plan }` (the freshly emitted, unapproved plan). */
export interface PlanEmitResponse {
  plan: PlanView;
}

/** A rendered char-offset span into a section's rendered text — M6 provenance click-through. */
export interface RenderedSpanView {
  span_id: string;
  start: number;
  end: number;
  /** The BARE registry id (e.g. "FACT_3") — display-inert until M6 wires click-through. */
  token_id: string;
}

/** A `{start, end}` char-offset range (the mechanical-splice target a finding carries). */
export interface SpanRefView {
  start: number;
  end: number;
}

/** One draft section for the compliance panel — the RENDERED preview + spans (never the body). */
export interface ComplianceSectionView {
  section_id: string;
  sort_order: number;
  validation: SectionValidation;
  /** Tokens resolved to display forms; the tokenized body is deliberately absent (inv 11). */
  rendered_preview: string | null;
  spans: RenderedSpanView[];
}

/** One G3 compliance finding as the wire projects it (mirror ComplianceFindingView). */
export interface ComplianceFindingView {
  id: string;
  draft_id: string;
  section_id: string;
  registry_version: number;
  check_kind: CheckKind | string;
  bucket: FindingBucket;
  severity: FindingSeverity;
  detail: string;
  anchors: unknown[];
  span: SpanRefView | null;
  status: FindingStatus;
  disposition: FindingDisposition | null;
  override_reason: string | null;
}

/** The latest draft summary — id/version/registry_version/status/memo (null when none exists). */
export interface ComplianceDraftView {
  id: string;
  version: number;
  registry_version: number;
  status: string;
  memo: string | null;
}

/** The G3 (compliance_review) view-model — the latest draft + sections + findings + counts. */
export interface ComplianceReviewVM {
  draft: ComplianceDraftView | null;
  sections: ComplianceSectionView[];
  /** Findings ordered blocking-first then oldest-first (the attorney works blockers first). */
  findings: ComplianceFindingView[];
  /** The exact count the G3 `no_blocking_findings` guard reads. */
  open_blocking: number;
  /** Routing summary over the OPEN findings: span-patchable vs regen. */
  buckets: { mechanical: number; semantic: number };
}

/**
 * POST /api/findings/{id}/action body — CLOSED. `override` REQUIRES a non-blank `override_reason`
 * (the FE client-validates non-blank before firing). `accept` may carry one too (advisory).
 */
export interface FindingActionBody {
  action: "patch" | "regen" | "accept" | "override";
  override_reason?: string;
}

/** POST /api/findings/{id}/action success (200) — the refreshed finding + the open-blocking count. */
export interface FindingActionResponse {
  finding: ComplianceFindingView;
  open_blocking: number;
}

/** One built artifact of a set — the kind-keyed download `url` (never the internal object_key). */
export interface ArtifactView {
  kind: string;
  sha256: string;
  byte_count: number;
  /** Same-origin GET — a plain `<a href>` triggers the browser-native download. */
  url: string;
}

/** One artifact set (a completed package build) — its versions + created_at + artifacts. */
export interface ArtifactSetView {
  id: string;
  draft_version: number;
  registry_version: number;
  /** ISO datetime string (or null). */
  created_at: string | null;
  /** BUS-05: true ONLY for the current (non-superseded) draft at the current registry
   *  version — historical sets stay downloadable but are never labeled current. */
  current: boolean;
  artifacts: ArtifactView[];
}

/**
 * The package_assembly / package_ready view-model — the artifact sets (latest first) + a
 * `buildable` flag (only meaningful at package_assembly: the latest draft is approved).
 */
export interface PackageVM {
  artifact_sets: ArtifactSetView[];
  buildable: boolean;
  /** BUS-05: the latest packaged set matches the matter registry (no late records since). */
  registry_version_current: boolean;
  /** BUS-05: at package_ready with new records, the attorney must start a NEW cycle. */
  new_cycle_required: boolean;
}

/** GET /api/matters/{id}/artifacts → `{ sets: [...] }` (the artifact-sets list). */
export interface ArtifactsResponse {
  sets: ArtifactSetView[];
}
