# WD-1 — Suppress the inapplicable public-entity notice-of-claim deadline for private-party intake

- Parent roadmap: `backlog/planned/workshop_demo_milestone.md` (demo-track slice WD-1); corresponds
  to plan-set slice S9 (`backlog/workshop_mvp_plan_set/umbrella.md:53`).
- Slice ID: WD-1 (owning plan-set slice: S9, full)
- Dependencies: none — after S1 (complete); independent of WD-2/WD-3/WD-4.
- Mergeability: independent.
- Deployment: safe — a candidate-set narrowing at matter creation; no schema, migration, route, or
  wire-shape change.
- Safe intermediate state: n/a — one small self-contained PR.
- Final integration owner: the WD-0 milestone acceptance (roadmap), not this slice.
<!-- sdlc-tier-assessment:start -->
## SDLC tier assessment
- SDLC-Tier: 3
- SDLC-Minimum-Tier: 3
- SDLC-Tier-Status: APPROVED
- SDLC-Tier-Assessor: Claude Opus 4.8, live repository context
- SDLC-Tier-Content-SHA256: 792ae64bb1c06e9ccf714b96037ec6370f96e10987cf6c4ceb5001ca2b1c7c14
- SDLC-Tier-Base-SHA: 814b75c27b7a6360d855280203d78e9df049ac29
- SDLC-Tier-Triggers: silent plausible-wrong result with legal materiality — a wrong notice-of-claim/SOL suppression predicate in _rule_applies drops a deadline candidate the attorney never sees at G1 (SOL/notice-of-claim malpractice impact)
- SDLC-Tier-Approval: user-approved in thread
- SDLC-Tier-Approval-Rationale: recommended — Tier 3 is the minimum; the legal-output-integrity trigger is not lowered by the pre-production wire-scope modifier
- SDLC-Tier-Degraded-Assurance: NONE
- SDLC-Tier-Revalidation: unchanged-tier — rebased to main (814b75c) for an independent PR; WD-1 affected code (deadlines/matters/service/az.yaml) is byte-identical between main and 2108662, so no affected-code drift; earlier round-1 consensus edits were test-mapping/enumeration corrections + a risky-test removal; Tier 3 and scope unchanged
<!-- sdlc-tier-assessment:end -->

## Goal and non-goals

- Goal: stop offering the public-entity notice-of-claim deadline candidate
  (A.R.S. § 12-821.01, 180-day) on matters whose intake explicitly answers "no public entity
  involved", while never dropping that candidate when a public entity is or may be involved.
- Observable success: a created v1 matter (MVA, private-party) carries only the SOL candidate; its
  deadline banner no longer shows a spurious notice-of-claim deadline; the SOL candidate and G1
  confirmation semantics are unchanged.
- Non-goals:
  - No rule-pack/YAML/loader change (would bump the pack fingerprint and break pinned matters via
    `load_pack_for_pin`); the applicability key stays in code for v1 AZ.
  - No structured pack field (`requires_public_entity`) — deferred to v2 as a documented seam.
  - No backfill/recompute of matters created before this slice (their stored `sol_candidates` are
    forward-only; pre-production has no durable matters to migrate).
  - No FE code change, no new route, no schema/migration, no change to G1 confirmation semantics.

## Live-code grounding

- Owner surface: `backend/app/rules/deadlines.py` — pure computation (invariant 3: no
  `datetime.now`; deadlines.py:3). Current applicability is `_rule_applies(rule, claim_type)`
  (deadlines.py:30-39): a `claim_type is None` rule "applies to every matter", which is why the
  notice-of-claim rule fires for private-party matters.
- Single production caller: `backend/app/api/routes/matters.py:93`
  `compute_deadline_candidates(pack, body.claim_type, body.incident_date)` — does not pass the
  intake answer. `body.public_entity_involved` is already validated, present, and persisted
  (matters.py:62, 109; schemas.py:467 `MatterCreate`; enums.py:394 `IntakeFlagAnswer`).
- Eligibility precondition: `check_pilot_eligibility` (matters.py:61-66) refuses any matter whose
  `public_entity_involved != NO` (YES out of box; UNKNOWN refuses conservatively — enums.py:394-400)
  BEFORE line 93. So at creation the answer is guaranteed NO today; the YES/UNKNOWN branches added
  here are correct-but-dormant until the pilot box widens (forward-compat, not dead code by intent).
- Rule data: `backend/app/rules/packs/az.yaml` has exactly two deadline rules — SOL/MVA (claim_type
  `mva`) and notice_of_claim (`claim_type: null`, `applies_when: "defendant may be a public
  entity"`, 180 days, § 12-821.01). `applies_when` is informational-only, not code-evaluated at v1
  (loader.py:48).
- Output consumers: `matter.sol_candidates` (orm.py:179, JSON) →
  (a) `matter_to_view` / `MatterView.deadline_candidates` (view_models.py:93) → FE
  `deadline-banner.tsx`; (b) `facts_review_vm` (view_models.py:264-290) → the G1 candidate
  confirmation payload; and (c) G1 `deadlines_all_confirmed` / `deadlines_confirmed`
  (service.py:15, 293) which requires the set NON-EMPTY and every candidate `confirmed=True`.
- Existing symbols/tests: `compute_deadline_candidates` (deadlines.py:42); its only DIRECT callers
  are matters.py:93 and the two unit calls in `backend/tests/rules/test_deadlines.py:31,43`
  (repo-wide grep: no other caller). BUT the OUTPUT — the created matter's candidate set — is
  observed by API-level tests that create matters through the real `POST /api/matters` and assert
  on / confirm the pack-computed notice_of_claim candidate; these ARE affected and must be updated
  (see the allocation notes): `test_matters.py::test_create_matter_returns_201_with_deadline_candidates`
  (asserts `{sol, notice_of_claim}`), and in `backend/tests/api/test_gates_api.py` the shared
  `_confirm_all_edits()` helper (confirms `NOC_CITE`), `test_current_envelope_facts_review_shape_for_attorney`
  (asserts `{SOL_CITE, NOC_CITE}`), `test_happy_g1_confirm_all_then_approve` +
  `test_duplicate_idempotency_key_replays` (both apply `_confirm_all_edits()`), and
  `test_list_matters_tenant_scoped_newest_first` (asserts two candidates). The repo-wide output
  consumer sweep also finds `test_m3_exit_flow.py::test_m3_exit_full_gate_flow_with_audit_trail`
  (two fixed two-candidate assertions) and
  `test_m4_exit_flow.py::test_m4_exit_full_g2a_flow` (one fixed two-candidate assertion); both are
  affected. `test_m5_exit_flow.py::test_m5_exit_full_demand_package` is excluded because it derives
  confirmations from the returned list and asserts no fixed candidate count; the other real-POST
  API fixtures do not inspect or hardcode the candidate set. Only
  `backend/tests/engine/test_gate_service.py` is genuinely independent — it builds synthetic
  `_candidates()` fixtures (test_gate_service.py:154), not the pack.
- Compatibility: `DeadlineCandidate` schema (schemas.py:90), `sol_candidates` column, and the pack
  version/fingerprint are all unchanged.

## Mechanism and the design decision

`_rule_applies` gains a third parameter `public_entity_involved: IntakeFlagAnswer`, and
`compute_deadline_candidates` gains it as a **required** parameter (no default — a silent default is
the exact silent-wrong-result this slice removes). Applicability:

- SOL / claim-type-scoped rule: applies iff `rule.claim_type == claim_type.value` (unchanged).
- `NOTICE_OF_CLAIM` rule (the public-entity trap): applies iff
  `public_entity_involved is not IntakeFlagAnswer.NO` — i.e. suppressed on an explicit NO, included
  on YES and on UNKNOWN. The fail-safe direction: uncertainty never drops a deadline.
- Any other `claim_type is None` rule (none today; defensive): unchanged "applies to every matter".

The caller passes `body.public_entity_involved` at matters.py:93.

Decision: key the gate on `kind is NOTICE_OF_CLAIM` in code, not on a new pack field.
- Chosen because a pack change alters `RulePack.fingerprint` (loader.py:191), and any matter pinned
  to the old fingerprint then raises `RulePackChanged` through `load_pack_for_pin` (loader.py:276) —
  a real compatibility blast radius. Code-side keying touches only `deadlines.py` + one caller line
  and leaves the pack byte-for-byte identical.
- Legally sound for v1 AZ: notice-of-claim (§ 12-821.01) is the public-entity mechanism, documented
  by the rule's existing `applies_when`.
- v2 seam (non-goal here): when a pack needs a non-public-entity notice_of_claim rule or a
  public-entity SOL rule, move the applicability key into the pack as a structured field. Recorded
  so the code coupling is intentional, not accidental.

## Data flow and blast radius

intake answer (`public_entity_involved`, guaranteed NO at creation by eligibility) →
`compute_deadline_candidates` → `_rule_applies` suppresses the notice_of_claim candidate →
`matter.sol_candidates` stores SOL-only → `MatterView.deadline_candidates` (one fewer item) → FE
banner renders SOL only; G1 requires only the SOL candidate confirmed.

- `sol_candidates` stays non-empty for MVA (the SOL/MVA rule always applies), so the G1
  `deadlines_confirmed` non-empty invariant (service.py:293) is preserved — the fix must never
  empty the set.
- Confirmation applies to existing candidates in place, keyed by `statute_cite`
  (test_gate_service.py:314; service.py:527-534); it cannot add a candidate, so a confirmation
  naming the suppressed § 12-821.01 cite on a SOL-only matter matches nothing and is refused
  FAIL-LOUD: `_apply_facts_review_edits` raises `UnknownDeadlineRule` → typed 422 and the WHOLE
  edit rolls back (service.py:530-532; existing `test_unknown_rule_id_refuses_whole_edit`). The
  suppressed candidate is never resurrected — the typed refusal is the guarantee, not a silent
  no-op (a silent-swallow would itself be a regression against that existing contract).
- Forward-only: matters created before merge keep their stored candidates (no recompute/backfill).

## Boundary and adversarial test matrix

| ID | Surface/path | Source → validator → owner → consumer → sink | Happy | Negative | Edge | Terminal/failure | Side effects present/absent | Exact deterministic test mapping |
|---|---|---|---|---|---|---|---|---|
| BM-01 | `_rule_applies` / `compute_deadline_candidates` applicability | az pack + intake answer → `_rule_applies` → candidate list | MVA + NO → SOL present, notice_of_claim absent | SOL/MVA candidate present for every answer (claim rules unaffected) | UNKNOWN → notice_of_claim present (fail-safe); YES → notice_of_claim present | candidate set never empty for MVA (SOL always present) | pure/deterministic (no `datetime.now`); surviving candidates keep exact date/cite/assumptions/verify_status | `happy no → backend/tests/rules/test_deadlines.py::test_notice_of_claim_suppressed_when_public_entity_no; happy yes → ::test_notice_of_claim_present_when_public_entity_yes; edge unknown → ::test_notice_of_claim_present_when_public_entity_unknown; negative sol-unaffected → ::test_sol_candidate_present_regardless_of_public_entity[no], ::test_sol_candidate_present_regardless_of_public_entity[yes], ::test_sol_candidate_present_regardless_of_public_entity[unknown]; edge non-empty → ::test_mva_candidate_set_never_empty[no], ::test_mva_candidate_set_never_empty[yes], ::test_mva_candidate_set_never_empty[unknown]; side-effect fields → ::test_surviving_candidate_fields_unchanged[sol-date-cite], ::test_surviving_candidate_fields_unchanged[notice-date-cite-when-present]` |
| BM-02 | Caller wiring in `create_matter` (matters.py:93) | `MatterCreate.public_entity_involved` → `compute_deadline_candidates` → stored `sol_candidates` → `MatterView.deadline_candidates` | created matter (eligibility ⇒ NO) → stored candidates = SOL only | route passes the real intake answer, not a hardcoded NO (regression against future eligibility widening) | only NO is reachable at the route today (YES/UNKNOWN refused upstream) — documented, tested at BM-01 not here | non-AZ / eligibility refusals still return their existing typed 422 (unchanged) | audit `matter_created` payload unchanged; matter fields unchanged except `sol_candidates` content; no new route/field | `happy → backend/tests/api/test_matters.py::test_created_matter_excludes_public_entity_notice_candidate; happy sol kept → ::test_created_matter_retains_sol_candidate; regression wiring → ::test_create_passes_intake_answer_to_deadline_computation; existing POST response observer → ::test_create_matter_returns_201_with_deadline_candidates (updated to SOL-only); M3 POST + G1 observer → backend/tests/api/test_m3_exit_flow.py::test_m3_exit_full_gate_flow_with_audit_trail (both fixed-count assertions updated to SOL-only); M4 G1 observer → backend/tests/api/test_m4_exit_flow.py::test_m4_exit_full_g2a_flow (fixed-count assertion updated to SOL-only); unchanged refusals → backend/tests/api/test_matters.py::test_create_eligibility_refusal_unchanged, ::test_create_non_az_refusal_unchanged` |
| BM-03 | Downstream G1/view invariant | `matter.sol_candidates` → `deadlines_all_confirmed` (service.py:293) + `matter_to_view` / `facts_review_vm` (view_models.py:93, 264) | SOL-only set: a confirmed SOL candidate → `deadlines_confirmed` True | empty set → False (unchanged; existing coverage) | matter and G1 views rehydrate a SOL-only set cleanly into typed `DeadlineCandidate` | confirmation naming the suppressed § 12-821.01 cite on a SOL-only matter → `UnknownDeadlineRule` typed 422, whole edit rolls back (NOT a silent no-op; service.py:530-532) | synthetic-fixture gate tests unaffected (they build `_candidates()` directly, not the pack); the suppressed candidate is never resurrected by a confirmation | `sol-only confirmable → backend/tests/engine/test_gate_service.py::test_deadlines_confirmed_with_sol_only_candidate; empty stays false → backend/tests/engine/test_gate_service.py::test_guard_context_empty_candidate_list_is_false (existing, test_gate_service.py:225 characterized); suppressed-cite confirm refused → backend/tests/engine/test_gate_service.py::test_confirmation_for_suppressed_cite_raises_unknown_rule (asserts `UnknownDeadlineRule`/422 + rollback, matching existing test_unknown_rule_id_refuses_whole_edit); matter view rehydrate → backend/tests/api/test_matters.py::test_matter_view_rehydrates_sol_only_candidates; G1 view observer → backend/tests/api/test_gates_api.py::test_current_envelope_facts_review_shape_for_attorney (updated to `{SOL_CITE}`); SOL-only confirm/replay consumers → ::test_happy_g1_confirm_all_then_approve, ::test_duplicate_idempotency_key_replays; list view observer → ::test_list_matters_tenant_scoped_newest_first (updated to one candidate)` |
| BM-04 | Forbidden effects — pack, schema, determinism, contract | this slice's diff | — | — | — | — | pack unchanged (version 0.1.0, 2 rules, fingerprint stable); no alembic version added; `compute_deadline_candidates` param is REQUIRED (no silent default); FE unchanged | `pack shape → backend/tests/rules/test_deadlines.py::test_az_pack_loads_as_unaudited_stub (existing version + two-rule guard); required-param → ::test_compute_requires_public_entity_argument; unchanged pack/schema/migration/FE surfaces → focused diff-scope verification, not a persistent repository-inventory test` |

Notes on allocation:
- BM-01 uses parametrized ids per behaviorally distinct outcome (NO suppresses; YES/UNKNOWN
  include), never one test per enum value sharing a path.
- Two `test_deadlines.py` unit tests change signature/expectation and are updated, not weakened:
  `test_candidates_for_mva_incident` and `::test_candidates_carry_cites_assumptions_and_unverified_status`
  pass `public_entity_involved` (YES) to still exercise the notice-of-claim candidate. Recorded in
  the implementation sequence.
- Existing API-level tests observe the pack-computed candidate set through the real
  `POST /api/matters` (eligibility ⇒ NO), so a NO-matter now carries SOL only; they are UPDATED,
  not weakened (dropping a real candidate to green a test is banned — these change to the correct
  post-suppression expectation):
  - `test_matters.py::test_create_matter_returns_201_with_deadline_candidates` — assert
    `set(kinds) == {"sol"}` and remove the `notice_of_claim` date lookup (lines ~49, 51);
    notice-present is exercised ONLY at BM-01 unit level because YES/UNKNOWN are unreachable through
    the eligibility-gated route.
  - `test_gates_api.py::_confirm_all_edits()` helper — drop the `NOC_CITE` confirmation; on a
    SOL-only matter, confirming `NOC_CITE` would raise `UnknownDeadlineRule` (422). This reconciles
    `test_happy_g1_confirm_all_then_approve` and `test_duplicate_idempotency_key_replays`, which
    apply the helper.
  - `test_gates_api.py::test_current_envelope_facts_review_shape_for_attorney` — assert the G1 VM
    candidates == `{SOL_CITE}`.
  - `test_gates_api.py::test_list_matters_tenant_scoped_newest_first` — assert one candidate per
    matter (`len(...) == 1`), not two.
  (`test_stale_payload_version_is_409_with_fresh` also calls `_confirm_all_edits()` but is refused
  at the version fence BEFORE edits apply, so it is unaffected; leave it.)
  - `test_m3_exit_flow.py::test_m3_exit_full_gate_flow_with_audit_trail` — change both the create
    response and G1 VM fixed-count assertions from two candidates to the exact SOL-only outcome;
    its dynamic confirm-all logic remains unchanged.
  - `test_m4_exit_flow.py::test_m4_exit_full_g2a_flow` — change its G1 VM fixed-count assertion
    from two candidates to the exact SOL-only outcome; its dynamic confirm-all logic remains
    unchanged.
- Absence-sweep exclusions: `test_m5_exit_flow.py::test_m5_exit_full_demand_package` dynamically
  confirms the returned list and has no fixed-count assertion; other real-POST API fixtures do not
  inspect or hardcode the candidate set. Synthetic `_candidates()` fixtures in
  `test_gate_service.py` exercise the unchanged generic confirmation contract.
- No migration/pack/schema/FE changes are verified from the implementation diff. Do not add a
  standing migration-file inventory test: it would either be vacuous or fail on a later legitimate
  migration unrelated to this slice.

## Independent matrix-completeness review

Scaffold — filled by the fresh-context attestation at the end of `plan-consensus-loop`.

<!-- matrix-attestation:start -->
- Reviewer/context: fresh Claude subagent (read-only, neutral inputs, did not draft or edit the plan) re-attested against base main
- Matrix-Completeness-Gate: PASS
- Matrix-Deferred-Findings: NONE
- Matrix-Review-Content-SHA256: 3d8c3f550b572813f7217f74da3db6d416cd30ac7ff1a63df09b8e1be0639e4a
- Matrix-Review-Base-SHA: 814b75c27b7a6360d855280203d78e9df049ac29
- Matrix-Review-Worktree: clean-except-plan
- Changed seams and fallback/legacy paths audited: PASS — BM-01–BM-04 cover the applicability function, caller wiring, downstream G1/view consumers, and forbidden effects
- Every populated axis → exact deterministic test mapping confirmed: PASS — tri-state (NO/YES/UNKNOWN) mapped to distinct parametrized ids; no per-enum test sharing a code path
- Producer failure + consumer response pairs confirmed: PASS — suppressed-cite confirmation → UnknownDeadlineRule/422 + full rollback (service.py:530-532; test_unknown_rule_id_refuses_whole_edit); empty-set → deadlines_confirmed False
- Forbidden side-effect assertions confirmed: PASS — pack shape/fingerprint stable, required param (no silent default), no schema/migration/route/FE change
- N/A axes and concrete reasons confirmed: PASS — route YES/UNKNOWN unreachable via eligibility (tested at BM-01 not the route); no migration-inventory test (vacuous/false-failing on later legitimate migrations); LLM omission (no model surface)
- Late-gap rule acknowledged: YES
<!-- matrix-attestation:end -->

## Red-test evidence before production code

- Commands: `cd backend && .venv/bin/pytest -q tests/rules/test_deadlines.py tests/api/test_matters.py tests/api/test_gates_api.py tests/api/test_m3_exit_flow.py tests/api/test_m4_exit_flow.py tests/engine/test_gate_service.py`
  (the API files are included because their real-POST matter observers are affected — see the
  allocation notes).
- Expected failures before code: the new BM-01/BM-02/BM-03/BM-04 tests fail because
  `compute_deadline_candidates` has no `public_entity_involved` parameter and the notice-of-claim
  candidate is still emitted for every matter; `test_created_matter_excludes_public_entity_notice_candidate`
  fails against current SOL+notice output. Conversely, AFTER the code lands (and until the affected
  existing tests are updated), the API-level tests that assert / confirm the notice candidate on a
  NO-matter turn red — `test_matters.py::test_create_matter_returns_201_with_deadline_candidates`,
  and in `test_gates_api.py` `test_current_envelope_facts_review_shape_for_attorney`,
  `test_happy_g1_confirm_all_then_approve` (422 `UnknownDeadlineRule` from the `NOC_CITE` confirm),
  `test_duplicate_idempotency_key_replays`, and `test_list_matters_tenant_scoped_newest_first`; these
  are updated to the post-suppression expectation in the same slice, never weakened. The same code
  change also turns red the two fixed-count assertions in
  `test_m3_exit_flow.py::test_m3_exit_full_gate_flow_with_audit_trail` and the fixed-count assertion
  in `test_m4_exit_flow.py::test_m4_exit_full_g2a_flow`; those are updated to the SOL-only outcome.
- Characterization to preserve: the current `test_candidates_for_mva_incident` proves the spurious
  candidate exists today (SOL + NOTICE_OF_CLAIM for a bare MVA call) — capture its current pass as
  the before-state, then update it to the new signature.
- Observed failures: not run yet (implementation not started).
- LLM integration omission: no model surface is touched.

## Implementation sequence

1. Add the BM-01–BM-04 test nodes to `backend/tests/rules/test_deadlines.py`,
   `backend/tests/api/test_matters.py`, and `backend/tests/engine/test_gate_service.py`; update the
   two existing signature-affected `test_deadlines.py` tests to pass `public_entity_involved`; AND
   update the existing API-level tests the suppression changes (per the allocation notes) —
   `test_matters.py::test_create_matter_returns_201_with_deadline_candidates` to SOL-only, and in
   `backend/tests/api/test_gates_api.py` the `_confirm_all_edits()` helper (drop `NOC_CITE`),
   `test_current_envelope_facts_review_shape_for_attorney` (VM == `{SOL_CITE}`), and
   `test_list_matters_tenant_scoped_newest_first` (one candidate); in
   `backend/tests/api/test_m3_exit_flow.py::test_m3_exit_full_gate_flow_with_audit_trail`, update
   both fixed-count assertions to SOL-only; and in
   `backend/tests/api/test_m4_exit_flow.py::test_m4_exit_full_g2a_flow`, update its fixed-count
   assertion to SOL-only. Capture red output + the current-state characterization.
2. Add `public_entity_involved: IntakeFlagAnswer` to `_rule_applies` and
   `compute_deadline_candidates` (required param); implement the tri-state gate on
   `kind is NOTICE_OF_CLAIM`.
3. Thread `body.public_entity_involved` at `matters.py:93`.
4. Run the focused tests, then `make test`, `make lint`, `make typecheck`, and `make verify`.

## Verification and acceptance

- All BM-01–BM-04 tests and every affected existing test allocated above pass; `make verify` green.
- A created MVA matter has `sol_candidates` = SOL only; the deadline banner shows no notice-of-claim
  item; G1 reaches `deadlines_confirmed` once the SOL candidate is confirmed.
- `backend/app/rules/packs/az.yaml`, the loader, the `sol_candidates` schema/column, and all routes
  are byte-unchanged; no alembic version added.
- Fail-safe confirmed by test: YES and UNKNOWN both retain the notice-of-claim candidate.
