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
}

/** UploadSlotView — one file slot; `upload_url` is where the client PUTs the bytes. */
export interface UploadSlotView {
  id: string;
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
export type GateAction = "approve" | "reject" | "edit" | "override";

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
  view_model: FactsVM | StrategyIntakeVM | MinimalGateVM;
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
  edits?: FactsReviewEdits | StrategyIntakeEdits;
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
