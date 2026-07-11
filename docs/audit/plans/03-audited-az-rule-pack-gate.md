# Audited Arizona Rule Pack Gate Implementation Plan

Findings covered: `BUS-02`

## Goal

Keep unaudited rule packs usable for local/demo workflows while preventing production-ready demand
packages from being built from unaudited legal rules.

## Current State

- `backend/app/rules/packs/az.yaml` is marked `audited: false`.
- The AZ pack has unverified deadline rules, billed-vs-paid basis, and letter-structure metadata.
- `backend/app/rules/loader.py` parses `audited` as informational.
- `backend/app/api/routes/drafting.py` builds packages without consulting the rule pack at all: the
  package SSE stream calls `build_artifact_set()` at `drafting.py:415` (inside `_package_stream`,
  route `POST /api/matters/{matter_id}/package/build`), and neither it nor
  `backend/app/package/build.py` calls `load_pack` — so there is no audited-pack gate anywhere in
  that path.
- `backend/app/package/build.py` builds and stores immutable artifacts once package assembly starts.

## Non-Goals

- Do not perform the legal audit in code.
- Do not expand beyond Arizona MVA in this plan.
- Do not block local tests and demos from exercising the unaudited pack unless a production flag is
  enabled.

## Implementation Steps

### 1. Extend rule-pack audit metadata

Files:

- `backend/app/rules/loader.py`
- `backend/app/rules/errors.py`
- `backend/app/rules/packs/az.yaml`
- `backend/tests/rules/test_deadlines.py`
- `backend/tests/rules/test_billed_vs_paid.py`
- `backend/tests/rules/test_letter_structure.py`

Plan:

1. Add audit metadata fields to `RulePack`:
   - `audited_by`
   - `audited_at`
   - `audit_reference`
   - `audit_notes`
2. Keep the metadata nullable while `audited is False`, but add a model-level invariant that
   rejects `audited: true` unless `audited_by`, timezone-aware `audited_at`, and
   `audit_reference` are non-empty. A boolean alone must never make a pack authoritative.
3. Add a `RulePack.is_authoritative` property or helper that requires:
   - `audited is True`
   - the required audit identity/reference metadata is present
   - `deadline_rules` is non-empty
   - every `deadline_rules[*].verify_status == verified`
   - `billed_vs_paid` is present and `billed_vs_paid.verify_status == verified`; the existing
     conservative fallback is acceptable for non-authoritative computation, but cannot support a
     production package
   - `letter_structure` is present and `letter_structure.verify_status == verified` (the AZ pack
     carries one at `az.yaml:46`; the plan-emit path consumes it via `letter_sections` at
     `backend/app/engine/brain2/plan.py:349`, and the package assembles the drafted sections that
     derive from that skeleton)
4. Add a typed rules-layer exception such as `RulePackUnaudited`, carrying jurisdiction/pack and
   version but no legal-source text, for consumers that require authority.
5. Add a deterministic `RulePack` fingerprint over the complete validated model (canonical JSON,
   stable key ordering, SHA-256). The fingerprint must change when audit metadata, verification
   status, or any behavior-affecting legal input changes; a mutable YAML path or version string alone
   is not sufficient provenance.
6. Keep the current AZ file unaudited until counsel actually reviews it; add the new metadata keys
   as null/omitted values consistent with the chosen schema.
7. Add tests showing the current AZ pack is valid but not authoritative; a fully verified pack with
   complete audit metadata is authoritative; and `audited: true` with missing metadata, no deadline
   rules, an omitted optional legal block, or any unverified row is rejected or remains
   non-authoritative as designed. Also prove the fingerprint is deterministic and changes for each
   authority-relevant mutation.

### 2. Add a package-build guard

Files:

- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/app/models/orm.py`
- `backend/alembic/versions/<new_rule_pack_pin_migration>.py`
- `backend/app/package/build.py`
- `backend/app/rules/loader.py`
- `backend/app/api/routes/matters.py`
- `backend/app/api/routes/evidence.py`
- `backend/app/api/view_models.py`
- `backend/app/api/routes/drafting.py`
- `backend/app/corpus/ingest/phase0.py`
- `backend/app/engine/brain1/analysis.py`
- `backend/app/engine/brain2/plan.py`
- `backend/app/engine/compliance/checks.py`
- `.env.example`
- `backend/tests/core/test_config.py`
- `backend/tests/test_health.py`
- `backend/tests/package/test_build.py`
- `backend/tests/api/test_matters.py`
- `backend/tests/api/test_analysis_api.py`
- `backend/tests/api/test_evidence_api.py`
- `backend/tests/api/test_package_api.py`
- `backend/tests/corpus/test_phase0.py`
- `backend/tests/engine/test_analysis_run.py`
- `backend/tests/engine/test_plan_emit.py`
- `backend/tests/engine/test_compliance_checks.py`

Plan:

Diagnostic prerequisite (must be completed before changing guard logic, per
`docs/debugging-policy.md`): add temporary debug-level instrumentation around the current package
build call that records only the matter id/jurisdiction, the pack's `audited` boolean, and whether
the build reached completion (no fingerprint, legal-source text, audit notes, or PHI). Reproduce a
package build with the current AZ pack and capture the log showing `audited=false` followed by a
successful build; that runtime evidence confirms the hypothesis that the package path accepts an
unaudited pack. Then implement the guard below, rerun the same reproduction to show refusal before
artifact work, and remove the temporary instrumentation (or retain only a non-PHI debug event if it
has ongoing diagnostic value).

1. Add a boolean setting such as `require_audited_rule_pack_for_package`, parsed with a strict
   boolean helper rather than Python truthiness.
2. Default it to `True` when `APP_ENV=prod`; default it to `False` in dev/test. Treat an explicit
   `false` override in production as invalid startup configuration (or remove the production
   override entirely), so a deployment cannot silently disable the legal gate. Wire this into the
   shared production settings validation/lifespan work if plan 01 lands first; otherwise add the
   equivalent validation to `backend/app/core/config.py`, call it from `backend/app/main.py` before
   `_seed_dev_environment()`, and keep `get_settings()` side-effect free.
3. Pin `rule_pack_version` and `rule_pack_fingerprint` on `Matter` when `create_matter()` loads the
   pack, using a new forward Alembic migration. Keep the columns nullable only for safe migration of
   existing rows; a missing pin is a hard refusal whenever the package guard is enabled. Do not
   backfill legacy matters from the current YAML, because that would falsely attest that earlier
   deadline, ledger, and drafting work used today's pack.
4. Enforce the authority check inside `build_artifact_set()` before the existing-set reuse lookup
   and before manifest/token minting, artifact rendering, storage writes, rows, or audit events.
   This keeps the safety invariant in the owning `app.package` domain rather than only in the
   wire-only API route, and prevents direct/background callers from bypassing it. Use the pinned-pack
   helper in the next step; only `app.rules` may read YAML. Require both that the current pack is
   authoritative and that its version and deterministic fingerprint exactly match the matter's pin.
   This prevents a matter processed under an unaudited or older pack from becoming buildable merely
   because the YAML is later audited or replaced.
5. Add one rules-owned `load_pack_for_pin(jurisdiction, version, fingerprint, *,
   require_authoritative)` helper and route every post-create rules consumer that can feed the
   package through it: Phase-0/analysis ledger sync, evidence-view/edit ledger recomputation,
   Brain-2 plan emission, compliance ledger hashing, and package build. For a pinned matter, reject
   version/fingerprint drift before that workflow's first write or commit; Phase-0 and analysis must
   preflight at entry and reuse the returned pack later, not wait until their current late ledger
   block after chronology/registry work. Otherwise a changed pack could be consumed during drafting
   and later reverted before package build, defeating a build-time-only comparison. When the
   audited-package setting is off, an unpinned legacy matter may retain the existing dev/test
   behavior, but a present pin must still match; when the setting is on, missing pins fail closed.
   Require full authority at package build; earlier stages need pin consistency but may continue to
   exercise an unaudited pinned pack so local/demo workflows remain usable. Map the typed refusal
   through each caller's existing REST/SSE failure boundary rather than leaking an uncaught 500,
   swallowing it as the existing unsupported-pack fallback, or partially advancing a stage.
6. Make the domain guard effective when
   `settings.app_env == "prod" or settings.require_audited_rule_pack_for_package`, not solely when
   the override is true. Startup validation remains defense in depth, but a direct/background
   package caller that does not run the FastAPI lifespan must still be unable to disable the gate in
   production.
7. Let `RulePackUnaudited` and typed missing/mismatched-pin errors propagate from `app.package`; map
   them in `_package_stream` to safe SSE `error` frames. For example, the unaudited case is:

   ```json
   {
     "phase": "package",
     "error": "rule_pack_unaudited",
     "jurisdiction": "AZ",
     "pack_version": "0.1.0"
   }
   ```

8. Map a missing legacy pin to `rule_pack_unpinned` and a version/fingerprint mismatch to
   `rule_pack_changed`; do not expose fingerprints on the wire. Also map `UnsupportedJurisdiction`
   to `jurisdiction_unsupported` and `RulePackInvalid` to
   `rule_pack_invalid` package SSE errors rather than allowing a mid-stream exception/torn
   connection. Do not expose exception strings, file paths, audit notes, or legal citations in the
   frame.
9. Leave `matter.gate_state` unchanged at `package_assembly`.
10. Do not reuse an existing `ArtifactSet`, mint registry tokens, write storage objects, rows, or
   audit events when the enabled authority check fails.
11. Add the setting and its production-safe semantics to `.env.example`; do not suggest a production
   break-glass value that startup rejects.

Tests:

- Add the regression/reproduction before the guard implementation: with the current AZ pack
  unaudited, exercise the existing package path and confirm it succeeds while the temporary
  diagnostic event records `audited=false`; after adding the setting/error types and guard, convert
  that same scenario to enable the requirement and assert the typed refusal.
- With the setting enabled and current AZ pack unaudited, package build streams
  `rule_pack_unaudited`, does not advance, and leaves storage/`ArtifactSet`/audit state unchanged.
- With the setting enabled, an already-built set is not reused after the pack becomes
  non-authoritative; the guard runs before the immutable-set reuse path.
- A matter created under the unaudited pack remains blocked after the current YAML is made
  authoritative, because its pinned fingerprint/version do not match; re-labeling mutable pack data
  cannot retroactively authorize prior work.
- Missing pins on legacy matters and changed version/fingerprint pins produce typed SSE errors and
  no partial side effects.
- A pack changed after matter creation is refused at each rules-consuming ledger/drafting/compliance
  stage before writes, and changing the file back cannot conceal that refusal; caller-level tests
  cover the typed REST/SSE boundary for each replaced `load_pack()` call.
- With a monkeypatched authoritative pack whose version/fingerprint match the matter pin, package
  build continues to the existing happy path.
- With the setting disabled, existing dev/test package build behavior remains unchanged.
- Invalid/unsupported pack failures produce typed SSE errors and no partial side effects.
- Config tests cover dev/test defaults, the production default, strict env parsing, and rejection of
  a production disable override/startup configuration.
- A lifespan/startup regression test proves the production validation is actually invoked before
  startup work, not merely defined in `config.py`.
- A direct `build_artifact_set()` regression test proves `APP_ENV=prod` still enforces the guard when
  a false override is injected and no application lifespan validation has run.

### 3. Surface the guard in package-ready UX

Files:

- `frontend/components/package-card.tsx`
- `frontend/lib/types.ts` (only if a typed frame is added — package SSE frames today use the
  generic `SseFrame` from `frontend/lib/sse.ts`)
- `frontend/__tests__/components/package-card.test.tsx`

Plan:

1. Display the package SSE `rule_pack_unaudited` error as a blocking package-build error: add a
   `rule_pack_unaudited` case to `buildErrorText` (`package-card.tsx:228-240`), whose default
   branch only echoes `data.detail`.
2. Add safe copy for the invalid/unsupported/missing-pin/changed-pack error codes added by the
   backend; do not render backend exception details, fingerprints, or legal-source metadata.
3. Do not call `onGateReady` or show a download-ready state when any rule-pack guard fails.
4. Keep wording concise: "Rule pack requires attorney audit before package build."
5. Do not add legal explanations in the frontend; the pack metadata and audit docs carry that.

### 4. Add legal-audit handoff documentation

Files:

- `docs/audit/rule-pack-audit-checklist.md` (`docs/audit/` already exists and holds the business
  completeness audit this checklist links from; there is no `docs/legal/` today)
- `docs/audit/business-function-completeness.md`
- `README.md` or deployment docs

Plan:

1. Create a checklist for counsel review:
   - statute citation
   - assumptions/tolling notes
   - billed-vs-paid source
   - demand-letter section order and required token kinds
   - reviewer, date, and source reference
2. Link the checklist from the business completeness audit.
3. Document that production package builds require authoritative packs, including the exact env
   setting/default and the fact that production cannot disable it.

## Rollout

1. Land metadata and authority helper with tests, updating
   `docs/module_contracts/app.rules.jurisdiction.md` for the new pack metadata/authority surface in
   the same pass. Because this changes required audit fields/fail-loud authority semantics, also
   update `docs/system_contract.md` §4/13 as required by that module's change rule.
2. Land the matter pack pin migration/create-path write and package-build guard behind an
   environment-derived setting. Document the persisted pin and legacy-row refusal in the applicable
   schema/system contract alongside the already-required updates.
   Update `docs/module_contracts/app.package.builder.md` for the domain guard and
   `docs/module_contracts/app.api.view_models.md` for every added REST/SSE rule-pack error code and
   shape across ingest, evidence, analysis, drafting, and package paths —
   plus, as those contracts' change rules mandate in the same PR: `docs/system_contract.md`
   §2/10/11 (the guard changes the builder's immutable-reuse rule and adds a build gate,
   `app.package.builder.md:153-170`) and §8/11/12/14 (new drafting-route error vocabulary,
   `app.api.view_models.md:161-180`), and a new ADR for the reuse/build-gate change (cf. ADR-0007).
3. Land frontend error handling and its regression tests in the same change as the new wire error.
4. After counsel review, update `az.yaml` in a legal-audit PR that changes the metadata and
   verification statuses.

## Verification

Run:

```bash
rtk test "cd backend && .venv/bin/pytest -q tests/rules tests/core/test_config.py tests/test_health.py tests/package/test_build.py tests/api/test_matters.py tests/api/test_analysis_api.py tests/api/test_evidence_api.py tests/api/test_package_api.py tests/corpus/test_phase0.py tests/engine/test_analysis_run.py tests/engine/test_plan_emit.py tests/engine/test_compliance_checks.py"
rtk test "cd frontend && npm run test -- package-card"
rtk make test
rtk make verify
```

## Acceptance Criteria

- Production package builds cannot create artifacts from an unaudited pack.
- Production configuration cannot disable the audited-pack requirement.
- Dev/test can still use the unaudited AZ pack unless the guard setting is enabled.
- The rule-pack authority check is deterministic, requires complete audit provenance plus every
  legal input used by drafting/package assembly to be verified, and is covered by tests.
- Package authority is checked against the exact pack version/fingerprint pinned when the matter was
  created; later edits or audit flips cannot retroactively authorize prior work, and legacy unpinned
  matters fail closed when the guard is enabled.
- All package entry points enforce the guard before reuse or side effects; typed pack failures leave
  the gate and persistence/storage/audit state unchanged.
- Legal audit metadata is explicit in the pack schema.
