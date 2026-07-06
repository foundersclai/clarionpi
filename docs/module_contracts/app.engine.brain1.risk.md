# app.engine.brain1.risk

Backs [`system_contract.md`](../system_contract.md) invariants **2, 6, 8, 13**.
Module path: `backend/app/engine/brain1`.
Design source: [`backlog/pi/components/risk_flag_engine.md`](../../backlog/pi/components/risk_flag_engine.md).

## Status

**Live @ M4.** The risk-flag engine is implemented and tested under
`backend/app/engine/brain1/risk.py`:

- `run_risk_detectors` — the idempotent detection run: re-derives open machine flags from the
  matter's encounters / strategy / incident, preserves dispositioned flags, and returns a
  `RiskRunOutcome` count block.
- `disposition_flag` — the G2a per-flag disposition act (role-gated, audited).
- `open_high_severity_count` — the shared guard-context feed (`severity == high AND disposition
  IS NULL`).
- `HighSeverityDispositionForbidden` — the typed refusal a route maps to a 403.

Semantic labeling composes `app.core.llm_telemetry.MeteredLLMClient` (the single metered door);
the deterministic detectors are pure code.

**Composition + wire (M4).** `run_risk_detectors` is run from the analysis composition point
`app.engine.brain1.analysis.run_analysis` — the `analysis_running -> evidence_review` build — as its
final stage (after the registry sync, chronology, and ledger AMT mint), so the flags exist by the
time G2a opens. Disposition is live over HTTP: `PUT /api/flags/{flag_id}/disposition`
(`app/api/routes/analysis.py::put_flag_disposition`) resolves the flag on the tenant-scoped session
(a cross-firm id → `404 flag_not_found`), calls `disposition_flag`, and maps the engine's
`HighSeverityDispositionForbidden` to a typed `403 role_forbidden{required, actual}` (invariant 8).
Success returns the extended `RiskFlag` view (incl. `detector` + `disposition_role`), scanned before
it leaves (inv 11).

**Guard parity (unchanged, verified).** `open_high_severity_count` and the orchestrator's
`build_guard_context` still count the identical predicate (`severity == high AND disposition IS
NULL`); the G2a-confirm guard `high_severity_dispositioned_or_override` reads it. An open HIGH flag
refuses the G2a approve `409 override_required`, or is proceeded over via an audited override — the
disposition path clears it, as the M4-exit E2E exercises end-to-end.

### M4 boundaries

- **Detector provenance vocabulary (`FlagDetector`).** Every flag records HOW it was produced.
  `DATE_MATH` is the deterministic-arithmetic bucket — it covers BOTH the `treatment_gap` date math
  and the `low_property_damage` amount comparison (pure code over authoritative fields; no LLM, no
  regex on clinical prose). `HEURISTIC_LLM` is the semantic labeling pass. (`LABEL` is the ORM
  default for legacy rows; this engine does not emit it — the two live producers are `DATE_MATH`
  and `HEURISTIC_LLM`.)
- **Idempotent re-run (flow_04).** A re-run **preserves** every flag that carries a `disposition`
  (attorney/paralegal work is never recreated or deleted) and **deletes + re-derives** every open
  (`disposition IS NULL`) machine flag (counted `replaced_open`). A freshly-derived candidate that
  matches a preserved dispositioned flag on `(kind, sorted (document_id, page) anchor set)` is
  **skipped** (counted `preserved_dispositioned`) so no duplicate ever appears. Dispositioned flags
  for records that later change re-enter via [flow_04](../../backlog/pi/system_flows/flow_04_late_records_rework.md).
- **`low_property_damage` is the one anchors-optional case.** It is intake-derived — the attorney's
  G1.5 `property_damage_estimate_cents` vs the threshold — so no page exists to cite and its
  `anchors` are `[]`. The detail names the G1.5 field as its source. Every OTHER flag carries page
  anchors.
- **LLM labels are page-set-validated (inv 2).** A label's `anchor_pages` are plain page ints,
  validated against the matter-wide valid `(document_id, page)` set (union of all encounter anchors
  + incident anchors). A cited page outside that set **rejects the whole label** (counted
  `anchors_rejected`, logged). The stored anchors become every valid `(document_id, page)` whose
  page number was cited (first match per page, deterministic doc order). **Per-encounter anchor
  precision** — disambiguating which document a shared page number belongs to — is bounded by M4
  and improves at S1/bbox time.
- **Per-kind cap is NOT suppression (inv 6).** `settings.risk_flag_per_kind_cap` exists for UI
  display grouping; the engine **never drops a finding**. Suppression of an adverse fact is the one
  thing this engine must not do — surfacing is the whole job.
- **Severity is clamped to the design taxonomy.** The LLM's claimed severity is trusted but
  **clamped** to the pinned per-kind table if it disagrees (preexisting / prior_claim /
  causation_ambiguity / liability_weakness / third_party_phi → high; degenerative_finding → medium).
  The clamp is deterministic policy, not a semantic rewrite of the label's text.
- **`need_more_records` leaves the flag OPEN (design D2).** It is a disposition, but it does not
  clear the flag: a G2a confirm over a still-open high-severity flag is `requires_override`, already
  enforced by the M0 guard `high_severity_dispositioned_or_override`. This engine does not special-
  case it — the flag simply carries the disposition and remains counted open only if its
  `disposition IS NULL` (which `need_more_records` is not; the guard's override path handles the
  "proceed eyes-open" case for flags the attorney has not resolved).
- **Disposition role rules (inv 8, server-enforced).** A HIGH-severity flag requires an attorney;
  a non-attorney raises `HighSeverityDispositionForbidden` (→ 403). Low/medium flags are
  prep-capable — a paralegal may disposition them. Re-disposition is allowed pre-freeze (an attorney
  may change their mind) and overwrites with a fresh audit event; post-confirm changes come through
  flow_04 rework, not this path.
- **Shared open-high-severity definition.** `open_high_severity_count` and the orchestrator's
  `build_guard_context` (`app.engine.orchestrator.service`) count the SAME predicate — `severity ==
  high AND disposition IS NULL`. The two must agree; this function is the named home for that count
  and the guard-context wiring mirrors it inline.

## Responsibility

Detect adverse / case-risk facts from the matter's already-extracted, already-tokenized facts and
**force a human disposition** before drafting. Emit anchored `RiskFlag` rows (deterministic +
LLM-labeled), and drive the G2a disposition act whose output becomes the address-list / no-volunteer
hard constraints for Brain-2 and the checks for the compliance panel. It owns the `RiskFlag` rows and
their dispositions, and it is the producer of the open-high-severity count the G2a-confirm guard
reads.

**Not responsible for:** the letter's rhetoric or *how* a risk is addressed (attorney at G1.5/G2.5
+ Brain-2); redaction *execution* (`app.package.builder` — this engine only routes `third_party_phi`
to it); any **arithmetic** (`app.money.ledger`) or minting tokens (`app.engine.tokenizer`); the G2a
confirm transition itself (`app.engine.orchestrator` — this engine only feeds the guard its count).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `RiskFlag` rows + their dispositions | — |
| Consumes | merged/tokenized `MedicalEncounter` rows | app.corpus.extraction |
| Consumes | `IncidentFacts` + `StrategyInputs` (MMI, property-damage estimate) | app.corpus.extraction / G1.5 |
| Consumes | metered semantic labeling (stage `analysis.risk_flags`) | app.core.llm_telemetry |
| Consumes | the actor (attorney / paralegal) for a disposition | app.engine.orchestrator (G2a) |
| Produces | anchored `RiskFlag` rows + `RiskRunOutcome` accounting | app.api.view_models (G2a VM) / brain2 |
| Produces | the open-high-severity count | app.engine.orchestrator (`build_guard_context`) |
| Produces | `risk_flags_generated` / `risk_flag_dispositioned` audit events | app.core.audit |

## Invariants enforced

- **[2]** Every LLM label carries page anchors validated against the matter's known page set or is
  rejected (`anchors_rejected`); the one anchors-optional case (`low_property_damage`) is
  intake-derived with no page to cite, recorded here explicitly.
- **[6]** Adverse facts are surfaced always, never suppressed: the per-kind cap is a UI display
  bound, not applied in the engine — every derived flag is persisted. (The no-volunteer discipline
  that keeps an undispositioned flag out of the letter is a downstream Brain-2 / compliance
  constraint; this engine guarantees the flags exist to be dispositioned.)
- **[8]** High-severity disposition is attorney-only, server-enforced (`HighSeverityDispositionForbidden`
  → 403); paralegals may disposition low/medium.
- **[13]** Semantic detectors are LLM (`HEURISTIC_LLM`), deterministic detectors are pure code
  (`DATE_MATH`) — no regex reads clinical prose to decide a semantic kind.

## Vocabulary

`RiskRunOutcome` (`deterministic_flags` / `llm_flags`, `anchors_rejected`, `llm_skipped`,
`preserved_dispositioned`, `replaced_open`) · `run_risk_detectors` (idempotent re-derive + persist)
· `disposition_flag` (role-gated G2a act) · `open_high_severity_count` (guard feed: `high` +
`disposition IS NULL`) · `HighSeverityDispositionForbidden` (`required_role`, `actual` → 403) ·
detector provenance `DATE_MATH` / `HEURISTIC_LLM` · flag-dedup key `(kind, sorted (document_id,
page) anchor set)` · labeling stage id `analysis.risk_flags` · audit kinds `risk_flags_generated`
/ `risk_flag_dispositioned` · severity **clamp** to the design taxonomy · per-kind cap is **display
grouping, never suppression**.

## Change rule

A boundary change requiring a contract update: changing the `RiskRunOutcome` shape; changing the
detector-provenance vocabulary or which detector owns which kind; changing the idempotent re-run rule
(open replaced / dispositioned preserved + the `(kind, anchor set)` dedup key); changing the LLM
anchor-page validation rule or the `low_property_damage` anchors-optional exception; changing the
severity-clamp taxonomy; changing the disposition role rules (high = attorney, others prep-capable)
or the re-disposition-overwrites rule; changing the `open_high_severity_count` predicate (it MUST
stay in lockstep with `build_guard_context`); changing the `analysis.risk_flags` stage id or the
`risk_flags_generated` / `risk_flag_dispositioned` audit kinds. Update this file **and**
[`system_contract.md`](../system_contract.md) §2/6/8/13 in the same PR.
