# WD-4 — Operator demo kit: talk track, disclosure, feedback, paid-review one-pager (thin S17)

- Parent umbrella: workshop **demo** track (WD-0 roadmap); operator-led workshop demo.
- Thins release-track slice: [`workshop_mvp_plan_set_s17_workshop_kit_evidence.md`](../workshop_mvp_plan_set/workshop_mvp_plan_set_s17_workshop_kit_evidence.md) (SRC-17); substitutes slide/verbal disclosure for the labeling in [`S13`](../workshop_mvp_plan_set/workshop_mvp_plan_set_s13_persisted_demo_identity.md).
- Slice ID: WD-4
- Dependencies: WD-3 (the scenario the talk track narrates). Narrates — does not modify — the already-live gate flow (M3–M6).
- Mergeability: independent (adds `workshop/demo_kit/` + one guard test; imports nothing in `app/`).
- Deployment: dormant materials — presenter-facing docs that create no product authority or legal approval.
- Safe intermediate state: docs only; nothing executes, nothing in `app/` imports them.
<!-- sdlc-tier-assessment:start -->
## SDLC tier assessment
- SDLC-Tier: 1
- SDLC-Minimum-Tier: 1
- SDLC-Tier-Status: APPROVED
- SDLC-Tier-Assessor: Claude Opus 4.8 (main session, direct read-only assessment)
- SDLC-Tier-Content-SHA256: f3bffbd6adeadb5f40ae4d3e7605b2bb70efce34e83397660311acf39c6a486b
- SDLC-Tier-Base-SHA: ed39c6c20cce5e69c2fd2c6cf155b6e6b9893c59
- SDLC-Tier-Triggers: none — isolated presenter materials + a pure text-scanning guard test; no schema/wire/state/prompt/provider/token/auth/money/concurrency/ownership change; disclosure legal accuracy is a human/attorney control outside the code tier
- SDLC-Tier-Approval: user-approved in thread
- SDLC-Tier-Approval-Rationale: recommended — natural Tier 1; docs-only materials slice with no app/ surface; compliance-sensitive disclosure guarded by BM-01/BM-03 and flagged for attorney review before the workshop
- SDLC-Tier-Degraded-Assurance: NONE
- SDLC-Tier-Revalidation: initial
<!-- sdlc-tier-assessment:end -->

## Goal and non-goals

- **Goal:** author the truthful **operator demo kit** for the 20-minute, operator-led, synthetic-only
  demo: a **talk track** (keyed to the real gate flow + the WD-3 scenario truth), a **disclosure**
  slide (the labeling substitute), an attendee **feedback form** (qualitative only — collects no
  client information), and an Arizona **paid-review one-pager**; plus a presenter handoff **checklist**.
- **Core trade-off (stated in the kit AND confirmed here):** slide/verbal disclosure substitutes for
  release-grade **S13 product-enforced labeling**. This is a judgment call valid **only because
  attendees never operate the product** — the demo is operator-led on a disposable database with
  owned-synthetic data and **no live intake**. At first attendee-/client-operated use the substitution
  expires and S13 labeling is required.
- **Observable success:** a non-builder can present the flow and its limitations from the kit, collect
  **zero** client information, and the disclosure distinguishes *demonstration* vs *intended attorney
  approval* vs *actual no-lawyer review*.
- **Non-goals:** no endorsement / equity / legal approval / live-client intake / PHI collection; **no**
  binding to an S13 evidence-export schema (that machinery is release-track S17/S13); no product or
  `app/` code change; no second scenario; no `make dev` runtime dependency.

## Live-code grounding

- **New owner (isolated):** `workshop/demo_kit/` — `talk_track.md`, `disclosure.md`, `feedback_form.md`,
  `paid_review_one_pager.md`, `README.md` (kit index + presenter checklist); guards in
  `backend/tests/workshop/test_demo_kit.py`. No `app/` seam is consumed or changed.
- **Narrated (not modified) product flow** the talk track walks: login → matter creation with pilot
  eligibility (WI-2) → upload the WD-3 corpus → phase0 classify/dedup/review-queue → **G1** facts +
  deadline candidates (WD-1: private-party → 2-yr AZ SOL, **no** public-entity notice-of-claim) →
  G1.5 strategy → G2a evidence / analysis + risk → **G2.5** plan approve → drafting (Brain-2) → **G3**
  compliance + approve (WD-2: current draft → `APPROVED` → `buildable`) → package build
  (letter/binder/chronology/provenance) → `package_ready` downloads + provenance click-through.
- **Scenario truth referenced** (from WD-3): *Rivas v. Doyle*, DOL 2025-03-14, AZ SOL **2027-03-14**
  (private-party, notice-of-claim N/A), grand billed **$29,050.00**.
- **Pre-run vs live:** the LLM-heavy beats run pre-meeting (results navigated live); gate approvals +
  package/provenance run live. The talk track marks each beat.
- **Contracts:** none changed. No schema/wire/state/prompt/route/ownership/`app.money` surface touched;
  `make hub-check` unaffected.

## Data flow and blast radius

Presenter reads the kit → narrates the pre-run results + drives the live gates/package/provenance.
**Blast radius: nil in `app/`** — the only new code is a text-scanning guard test. Reversible by
deleting `workshop/demo_kit/`.

## Boundary and adversarial test matrix

Tier-scoped to the kit's **safety invariants** (the S17 guardrails), the only machine-checkable
surface a materials slice has. Tests live in `backend/tests/workshop/test_demo_kit.py`.

| ID | Surface | Happy | Negative/Edge | Deterministic test mapping |
|---|---|---|---|---|
| BM-01 | disclosure completeness (S13 substitute) | `disclosure.md` carries every required element — demonstration/pre-production; owned-synthetic, no real PHI; not legal advice / no attorney-client relationship; output **not** attorney-reviewed/approved work product; the explicit S13-substitution note | a missing required element fails loud | `backend/tests/workshop/test_demo_kit.py::test_disclosure_has_all_required_elements` |
| BM-02 | no client-information collection | `feedback_form.md` collects only qualitative feedback | it contains **none** of a client-PII field denylist (name/DOB/SSN/claim #/diagnosis/contact) | `backend/tests/workshop/test_demo_kit.py::test_feedback_form_collects_no_client_information` |
| BM-03 | no overclaiming; distinguishes review states | no kit doc claims endorsement/approval/guarantee/equity; disclosure distinguishes *demonstration* vs *intended attorney approval* vs *actual no-lawyer review* | a forbidden overclaim token anywhere in the kit fails loud | `backend/tests/workshop/test_demo_kit.py::test_kit_makes_no_endorsement_or_approval_overclaim` |

<!-- matrix-attestation:start -->
- Reviewer/context:
- Matrix-Completeness-Gate:
- Matrix-Deferred-Findings:
- Matrix-Review-Content-SHA256:
- Matrix-Review-Base-SHA:
- Matrix-Review-Worktree:
- Changed seams and fallback/legacy paths audited:
- Every populated axis → exact deterministic test mapping confirmed:
- Producer failure + consumer response pairs confirmed:
- Forbidden side-effect assertions confirmed:
- N/A axes and concrete reasons confirmed:
- Pre-implementation findings resolved and plan re-reviewed:
- Late-gap rule acknowledged:
<!-- matrix-attestation:end -->

## Red-test evidence before production code

- Commands: `backend/tests/workshop/test_demo_kit.py` (BM-01..BM-03) run before `workshop/demo_kit/`
  exists.
- Expected failures: file-not-found on the kit docs, then assertion failures on missing disclosure
  elements.
- Characterization exception: the tests assert **fixed authored safety invariants** (disclosure
  present, no PII collection, no overclaim), not prose quality.
- LLM-integration note: materials are model-free and every test is pure/offline (no DB, no provider,
  no network).

## Implementation sequence

1. `disclosure.md` — the disclosure slide + speaker notes, carrying every BM-01 element and the
   explicit S13-substitution trade-off (valid only because attendees never operate the product).
2. `feedback_form.md` — qualitative attendee feedback only; **no** client-PII fields (BM-02).
3. `paid_review_one_pager.md` — Arizona paid-review outreach as a **separate written engagement**;
   no equity/endorsement/approval claims.
4. `talk_track.md` — the 20-minute beat script keyed to the live flow + WD-3 scenario truth, marking
   each beat pre-run vs live.
5. `README.md` — kit index + presenter handoff **checklist** (the roadmap's "plan/checklist").
6. `test_demo_kit.py` (BM-01..BM-03); red → green; `make verify`.

## Verification and acceptance

- `make verify` passes (guard test green). The demo-track **rehearsal** acceptance (two end-to-end UI
  passes on a fresh disposable DB) is a milestone activity (roadmap D5 / release S19), **not** WD-4.
- A non-builder can present the flow + limitations from the kit and collects no client information;
  the disclosure is present and distinguishes the three review states; the S13-substitution trade-off
  is explicit and scoped to operator-led use.
- No `app/` code, schema, wire, contract, or `app.money` surface is touched (`make hub-check` clean).
