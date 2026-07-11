# Business Function Completeness

Static audit date: 2026-07-06

Reviewed scope: matter creation, jurisdiction/rule packs, upload and ingest, OCR/classification,
extraction, fact registry, G1-G3 gates, Brain-1 analysis, Brain-2 strategy/drafting/compliance,
package builder, provenance, and the Next.js workbench.

## BUS-01 - Product scope is Arizona motor-vehicle matters only

Priority: Critical

Finding: The MVP currently supports a narrow personal-injury slice: Arizona jurisdiction and MVA
claim type.

Evidence:

- `backend/app/api/routes/matters.py` limits supported jurisdictions to `["AZ"]`.
- `backend/app/models/enums.py` defines `ClaimType.MVA`.
- `backend/app/rules/packs/az.yaml` is the only observed jurisdiction rule pack.

Impact: This may be the correct MVP scope, but it is not a general personal-injury demand platform
yet. Non-AZ matters, premises liability, med-mal, dog bite, product liability, and other common PI
flows are outside the current functional coverage.

Proposed plan:

1. Make AZ MVA the explicit product scope in the UI, README, onboarding, and matter creation copy.
2. Add rejection messages that explain unsupported jurisdictions/claim types without implying a
   system error.
3. Rank the next jurisdictions and claim types by target-firm case volume.
4. For each new scope, add a rule pack, legal audit, test fixtures, and sample package review before
   exposing it in the frontend.

## BUS-02 - Arizona legal rules are explicitly unaudited stubs

> Counsel handoff: [rule-pack-audit-checklist.md](rule-pack-audit-checklist.md) — the
> per-rule checklist that flips a pack authoritative; production package builds enforce
> authority via the matter pin + build gate (ADR-0011).

Priority: Critical

Finding: The AZ rule pack intentionally marks legal rules and drafting structure as unaudited and
unverified.

Evidence:

- `backend/app/rules/packs/az.yaml` says `STATUS: UNAUDITED STUB`.
- The pack has `audited: false`, `verify_status: unverified`, and statute/source strings marked
  `verify - counsel`.

Impact: The app can compute candidate deadlines and demand content, but those outputs should not be
treated as attorney-approved legal rules. This is a release blocker for production use unless the
product intentionally remains demo-only.

Proposed plan:

1. Have licensed Arizona counsel audit every rule, assumption, citation, demand basis, and drafting
   section definition.
2. Add reviewer name, review date, and source notes to the pack metadata.
3. Gate production package readiness on `audited: true` for the active pack.
4. Keep the current unverified markers visible in demo/dev if unaudited packs remain usable there.

## BUS-03 - No live LLM is configured by default

Priority: High

Finding: The no-LLM path is fail-visible, but core business workflows depend on classification,
extraction, narrative generation, drafting, and compliance model calls.

Evidence:

- `backend/app/core/llm_provider.py` defaults `LLM_PROVIDER` to `null`, returning `NullProvider`.
- The provider docstring says `null` refuses calls and degrades classification/extraction to review
  paths.
- `backend/app/core/config.py` defines model settings for classifier, extractor, drafter, judge,
  and memo stages.

Impact: A clean local/dev install can demonstrate workflow shape, but not a complete automated
demand-package workflow unless a live provider or scripted test provider is configured.

Proposed plan:

1. Define a supported demo/prod LLM setup path with explicit key management and vendor approval.
2. Add a synthetic-data smoke command that exercises classification, extraction, plan, draft,
   compliance, and package generation.
3. Add a visible environment health check that reports whether each model-backed stage is runnable.
4. Keep the `null` provider for tests and offline development, but label the resulting workflow as
   limited.

## BUS-04 - OCR is local/optional and defaults to none

Priority: High

Finding: OCR defaults to `none`, with optional fake and local Tesseract engines. There is not yet a
production OCR vendor path.

Evidence:

- `backend/app/core/config.py` sets `ocr_engine="none"` by default.
- `backend/app/corpus/ocr.py` implements `none`, `fake`, and `tesseract`; unknown values raise.
- Repo guidance says image-only pages flag `zero_text` unless Tesseract is installed and selected.

Impact: Scanned medical records, handwritten bills, and image-only police reports are common in PI.
Without production OCR, meaningful facts can stay unavailable or require manual remediation.

Proposed plan:

1. Select and approve a BAA-compatible OCR provider, or operationalize Tesseract with quality and
   scaling expectations.
2. Add OCR quality evaluation fixtures for scanned bills, medical records, EOBs, and police reports.
3. Add a frontend work queue for `zero_text` or low-text-density pages.
4. Track OCR confidence and route low-confidence pages to human review before extraction.

## BUS-05 - Late-document invalidation is incomplete after evidence review

Priority: High

Finding: The late-document path handles the `evidence_review` re-run transition, but comments and
logic indicate other mid-flow states are deferred or leave gate state untouched.

Evidence:

- `backend/app/corpus/ingest/phase0.py` documents late-document processing and says only the
  evidence-review case fires the analysis re-run transition.
- The same module notes other mid-flow states leave gate state untouched at the current boundary.

Impact: Adding a late record after strategy, draft, compliance, or package work can leave downstream
outputs stale unless a user manually understands what needs to be regenerated.

Proposed plan:

1. Define an invalidation matrix for every workflow state from upload through package-ready.
2. Mark affected strategy plans, drafts, compliance findings, package manifests, and artifacts as
   stale when new documents alter the fact registry.
3. Surface stale status in the frontend and block final package approval until regeneration.
4. Add regression tests for late documents inserted at evidence review, strategy review, drafting,
   compliance, and package-ready states.

## BUS-06 - Frontend upload can attach the wrong bytes to backend slots

Priority: High

Finding: The frontend maps `session.slots` to selected `File[]` by array index, while the backend
explicitly says slot order is deterministic but not guaranteed to equal client registration order.

Evidence:

- `frontend/components/documents-panel.tsx` uploads with `session.slots.map((slot, index) => {
  const file = files[index]; ... })`.
- `backend/app/corpus/ingest/sessions.py` says returned slots are ordered by `(created_at, id)` and
  are "NOT guaranteed to equal client registration order".

Impact: Multi-file upload can store a file's bytes under another file's slot/filename. That can
corrupt document identity, provenance, and attorney trust in the package.

Proposed plan:

1. Add a stable client file identifier or ordinal to `UploadSlot`.
2. Return that identifier in the upload-session view model.
3. Have the frontend match files by identifier, not array index.
4. Add backend and frontend regression tests with intentionally shuffled slots.

## BUS-07 - Time-limited demands are not supported

Priority: Medium

Finding: The strategy/drafting model leaves a seam for time-limited demands, but v1 only accepts
`"open"`.

Evidence:

- `backend/app/models/schemas.py` defines `demand_type: Literal["open"] | None`.
- `backend/app/engine/brain2/plan.py` comments that time-limited demand is a later seam.
- `frontend/components/plan-review-card.tsx` documents a fixed `"open"` demand type chip.

Impact: Time-limited demands are a meaningful attorney workflow. The current MVP cannot manage
deadline terms, insurer response dates, or the extra compliance review those demands require.

Proposed plan:

1. Add `time_limited` as a demand type with required expiration date/time, recipient, delivery
   method, and jurisdiction-specific constraints.
2. Add rule-pack support for any state-specific timing and notice requirements.
3. Add compliance checks that verify the final letter carries required terms consistently.
4. Add UI controls for deadline entry, review, and warnings.

## BUS-08 - Package letter generation is generic and not firm-template driven

Priority: Medium

Finding: The package builder can generate artifacts, but firm-specific template ingestion and
letterhead handling appear to be deferred.

Evidence:

- `backend/app/package/artifacts.py` is a pure artifact builder for generated letter and
  chronology bytes.
- Root docs describe the package builder as assembling `letter.docx`, `binder.pdf`,
  `chronology.xlsx`, and `provenance_report.pdf`, but no firm-template management surface was found.

Impact: Personal-injury firms usually need demand letters to match their letterhead, signature
blocks, formatting, and carrier-facing conventions. Generic output may be acceptable for internal
review but weak for production adoption.

Proposed plan:

1. Add firm-level DOCX template upload/validation with required placeholders.
2. Store template version metadata and associate generated packages with the template version used.
3. Add deterministic rendering tests for representative templates.
4. Provide a fallback built-in template only when no firm template is configured, and label it as a
   default.

