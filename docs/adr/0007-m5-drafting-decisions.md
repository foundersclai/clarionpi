# ADR-0007: M5 drafting / compliance / package decisions

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** foundersclai

## Context

M5 lands the second half of the pipeline on top of the M4 gate machine + evidence workbench: Brain-2
(`app/engine/brain2` — plan emit, the per-section drafter, the deterministic validator, the renderer,
the strategy memo, and the `drafting -> compliance_review` run), the G3 compliance panel
(`app/engine/compliance` — deterministic checks + the Sonnet judge + the finding lifecycle +
corrections), and the package builder (`app/package` — the four immutable artifacts, Bates, and the
provenance report), plus the D1 wire that puts all three on HTTP (`app/api/routes/drafting.py`). The
G2.5 plan-approve and G3 package-kick side effects that M4 registered as no-ops are now real.

This ADR records the ten decisions that set a boundary M6 builds on or that are expensive to reverse.
Each keeps M5 shippable and offline-testable (the `null`-provider path degrades visibly — plan
emphasis empties, the memo empties, the judge is honestly `judge_skipped` — and a `ScriptedProvider`
drives the LLM-backed paths in tests), and each names the heavier decision it defers. The gate-action
service shape is ADR-0005; the G2a manifest read-model it consumes is ADR-0006.

## Decision

We adopt the following ten decisions for the M5 drafting / compliance / package waves.

1. **Plan emit is an explicit NON-SSE POST — GETs never spend.** `POST /plan/emit` is a single
   bounded Opus emphasis call over the deterministic section skeleton, so it returns a `200` JSON
   plan view, not a stream (there is no per-step progress to show). The G2.5 GET view-model
   (`plan_review_vm`) is budget-free and never triggers the emit — a plan is emitted only by the
   explicit POST, so opening the gate screen is fast and spends nothing. The emit degrades visibly
   when the provider is offline (`emphasis_directives: []`), never blocking. *Rollback:* if the
   emphasis pass ever grows into a multi-step synthesis, promote the route to SSE and add the frames
   — the deterministic allocation would still precede any model call.

2. **Regen fix-instructions ride `retry_violations` — snapshot-neutral.** A single-section regen
   (`corrections.request_section_regen`) hands the finding's detail to the drafter through the
   `retry_violations` prompt-tail channel, NOT as an extra `HardConstraintInputs` entry — a
   deliberate deviation from a literal "add a hard-constraint entry" reading. `retry_violations` does
   not enter the `DrafterPromptSnapshot`, so the regenerated section's `input_hash` still reproduces
   from `build_hard_constraints` + the planned contract; the re-verify judge therefore never
   spuriously raises `SnapshotDrift` on a legitimately-regenerated section. Appending a hard-constraint
   entry would move the snapshot hash and break drafter↔judge symmetry (inv 13) — this is the
   `SnapshotDrift` root-cause fix. *Rollback:* if a fix ever MUST change the binding constraints (not
   just the prompt tail), re-emit the plan (a new `registry_version`-bound plan) rather than mutating
   one section's snapshot inputs — that keeps the symmetry contract intact.

3. **Span-patch is a deterministic re-render with a runtime escalation to regen.**
   `corrections.apply_span_patch` is the mechanical fix: it re-renders the whole section
   (`renderer.render_section`, no LLM), so an AMT fix lands by re-resolution and an exhibit fix by an
   upstream re-mint. If the re-rendered section then fails deterministic validation, the patch is
   abandoned and the finding is escalated to the semantic bucket (regen) — the TM
   span-patch-with-runtime-fallback safety net: a mechanical splice that would ship an invalid section
   falls back to a full regen rather than shipping the splice. *Rollback:* none needed — the fallback
   is strictly safer than shipping an unvalidated splice; removing it would let a bad splice reach the
   letter.

4. **A judge double-failure is a fail-visible manual-review finding, never a silent pass.** When the
   Sonnet judge returns no valid verdict for a section after its one stricter retry, the pass does NOT
   treat the section as clean — it emits one BLOCKING finding the attorney dispositions. The finding
   reuses the `tone` `check_kind` (the generic semantic kind) so the marker rides the semantic bucket
   WITHOUT inventing a new `check_kind` (which would break the `JudgeFindingBatch` schema gate that
   rejects a mechanical kind from the judge). *Rollback:* if the reuse of `tone` proves confusing in
   the panel, add a dedicated `judge_unavailable` semantic kind (a `check_kind` + schema + bucket
   addition, an inv-13 change requiring a contract edit) rather than dropping the fail-visible marker.

5. **All findings are BLOCKING at v1; the override effect rides the lifecycle, not the severity.**
   `_severity_for` returns `BLOCKING` for every kind the panel emits (the hard set, `prose_total_mismatch`,
   and the judge's semantic findings). `FindingGating.ADVISORY` is reserved for a later policy and is
   unused at v1: `open_blocking_count` keys off `status` (a finding drops out of the count when it is
   `re_verified` or `dispositioned`), so an attorney override clears the block through the DISPOSITIONED
   status rather than through an ADVISORY severity. BLOCKING-everywhere is the honest v1 default.
   *Rollback:* introduce ADVISORY findings (a severity a fresh finding can carry) only when there is a
   finding kind the attorney should see but never has to disposition — a policy change, not a code
   tweak, since the count semantics move.

6. **The provenance report is inline in the ArtifactSet AND an independently re-runnable module.**
   `provenance.build_provenance_report` is a side-effect-light builder (reads the DB, writes no rows)
   that `package.build` calls as one of the four artifacts, so the report ships in the immutable set;
   but because it takes only `(matter, draft, sections, flags)` and re-derives everything live, it can
   be re-run standalone — the M6 export seam (a fresh provenance render on demand) needs no new build.
   Its completeness property (Part 1 has exactly one fact entry per rendered span across the sections)
   is asserted in the M5-exit E2E. *Rollback:* none needed — inline + re-runnable are compatible; if
   M6 needs a different report shape it wraps this builder, it does not replace the inline artifact.

7. **Regen happens in place at `compliance_review`; the machine's regen round-trip is reserved for
   the FE long-form flow.** The engine drives a single-section regen WITHOUT the
   `(COMPLIANCE_REVIEW, SEMANTIC_FINDING_REGEN) -> DRAFTING` machine round-trip: the engine stays
   state-agnostic, re-verify covers correctness, and the gate never advanced, so re-drafting a section
   in place is sound. The machine edge still exists (the API wave may wire it) for the FE's long-form
   "kick the whole draft back to drafting" flow. *Rollback:* if in-place regen ever needs to reflect
   as a visible `drafting` state to the FE, wire the machine event at the route — the engine's in-place
   behavior does not change.

8. **The letter skeleton is a lawyer-audited YAML block, unverified pending counsel.** The demand
   letter's section skeleton (`letter_structure` in the jurisdiction pack) is pack data, never invented
   in code — a pack with no skeleton raises `LetterStructureMissing` and Brain-2 refuses to draft (fail
   loud). The v1 letterhead is a GENERATED firm-name heading + rule, not an uploaded firm template
   (template ingestion is a recorded open question). The skeleton's section set and wording are pending
   the legal cofounder's audit. *Rollback:* replace the generated letterhead with an uploaded-template
   ingestion path once counsel signs off on the letter structure — a pack-data + builder change, not a
   drafter change.

9. **The strategy memo is a matter artifact, excluded from the letter.** `generate_memo` produces an
   attorney-visible framing memo stored on `DemandDraft.memo` and shown at G2.5/G3; it is NEVER sent to
   the carrier — `build_letter_docx` accepts the `memo` parameter (stable signature) but deliberately
   does not write it into `letter.docx` (surfacing the reasoning that shaped the demand to the carrier
   would contradict the transparency posture the memo exists for). *Rollback:* if the memo should ever
   be a deliverable, emit it as its OWN artifact kind (an `ArtifactKind` addition), never fold it into
   the carrier letter.

10. **Bates is continuous with a config prefix; the defaults await the legal-cofounder call.** The
    binder stamps continuous Bates numbers `f"{prefix}{n:05d}"` starting `00001` AFTER the (unstamped)
    index page, in manifest order, deterministically (same manifest → identical numbers). The prefix is
    `settings.bates_prefix` (default `"CP"`). Whether the firm wants per-exhibit restart, a different
    width, or a matter-scoped prefix is an OPEN QUESTION for the legal cofounder — the continuous
    matter-wide scheme is the v1 default, not a settled policy. *Rollback:* add a Bates-mode setting
    (continuous vs per-exhibit-restart) once counsel decides; the numbering is computed in one place
    (`binder.build_binder_pdf`), so the mode is a localized change.

## Consequences

- The demand half of the pipeline is end-to-end runnable and testable offline at M5: the M5-exit E2E
  drives the full arc over HTTP (Phase 0 → G1 → G1.5 → analysis → G2a → plan emit → G2.5 → drafting →
  compliance → G3 → package build → `package_ready`), asserts `letter.docx` has zero unresolved tokens
  and the ledger-exact grand-billed display form, and proves the built package is immutable (a
  registry bump at `package_ready` is an `IllegalTransition`). The `null`-provider path degrades
  visibly rather than stalling at every LLM seam.
- Each decision names its later counterpart (an SSE emit, a plan re-emit for binding changes, a
  dedicated judge-unavailable kind, an ADVISORY severity, an M6 export wrapper, a machine-visible
  regen, template ingestion, a memo artifact kind, a Bates-mode setting) so the deferral is traceable.
- Two symmetry contracts are load-bearing and codified: the drafter↔judge `input_hash` (a drift fails
  the pass loudly, never grades a drifted world) and the AMT re-verify against the live ledger hash (a
  billing edit after render is caught at G3, never trusted). Decisions 2 and 3 exist specifically to
  keep the first intact across a regen.
- The four artifacts are byte-deterministic (pinned metadata + reportlab `invariant=1` + a fixed pypdf
  file `/ID`), keyed by `(matter, draft_version, registry_version)`, and immutable — a rebuild after
  drift is a NEW set, and the object_key never reaches the wire.

## Alternatives Considered

- **Make plan emit an SSE stream** — rejected: a single bounded call has no per-step progress to show;
  a `200` JSON plan view is the honest shape and keeps the GET fast. *Rollback:* above (1).
- **Pass regen fix-instructions as a hard-constraint entry** — rejected: it would change the drafter
  snapshot hash and make the re-verify judge raise `SnapshotDrift` on every legitimate regen, breaking
  inv-13 symmetry; the snapshot-neutral `retry_violations` channel is the root-cause fix. *Rollback:*
  above (2).
- **Ship a span-patch splice even if it fails validation** — rejected: a mechanical splice that lands
  an invalid section would reach the letter; the runtime escalation to regen is strictly safer.
  *Rollback:* none (3).
- **Pass a section the judge could not grade as clean** — rejected: silently clearing an ungradable
  section defeats the surface-always intent; the fail-visible `tone` marker routes it through the
  attorney disposition. *Rollback:* above (4).
- **Introduce ADVISORY findings now** — rejected for v1: every kind the panel emits is a genuine block,
  and the override effect already rides the DISPOSITIONED status; an unused severity would be dead
  policy. *Rollback:* above (5).
- **A separate provenance-export module distinct from the inline artifact** — rejected: the inline
  builder is already re-runnable (it writes no rows and re-derives live), so a second module would
  duplicate it; M6 wraps it. *Rollback:* none (6).
- **Route in-place regen through the machine's `SEMANTIC_FINDING_REGEN` edge** — rejected for the
  engine: the engine stays state-agnostic and the gate never advanced, so a machine round-trip is
  unnecessary work; the edge remains for the FE's long-form flow. *Rollback:* above (7).
- **Ingest an uploaded firm-template letterhead now** — rejected for M5: the letter structure is
  pending counsel's audit, so a generated letterhead is the honest v1; forcing template ingestion
  before the skeleton is signed off would build on unverified structure. *Rollback:* above (8).
- **Emit the strategy memo into the carrier letter** — rejected: the memo is internal framing; sending
  it to the carrier contradicts the transparency posture it exists for. *Rollback:* above (9).
- **Per-exhibit Bates restart as the v1 default** — rejected: continuous matter-wide numbering is the
  simpler, deterministic default and the firm's preference is unknown; a Bates-mode setting lands once
  counsel decides. *Rollback:* above (10).
