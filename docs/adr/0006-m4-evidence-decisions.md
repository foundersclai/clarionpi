# ADR-0006: M4 evidence-workbench (G2a) decisions

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** foundersclai

## Context

M4 lands the evidence-review (G2a) surface on top of the M3 gate machine: the Brain-1 analysis
composition (`analysis_running -> evidence_review`), the anchored risk-flag engine + its disposition
workflow, the specials-ledger G2a edit path, the chronology overlay store + wire, and the draft
binder **manifest read-model** (the M5 exhibit-binder preview). The wave is deliberately backend +
read-model only — nothing here builds a PDF, and the drafter (Brain-2) + compliance panel that consume
G2a's output are still M5 stubs.

This ADR records the eight decisions that set a boundary M5 builds on or that are expensive to
reverse. Each keeps M4 shippable and offline-testable (the `null`-provider path degrades visibly:
deterministic flags + the ledger mint still run and still advance the gate), and each names the heavier
decision it defers. The role guards + audit substrate they build on are ADR-0004; the gate-action
service shape is ADR-0005.

## Decision

We adopt the following eight decisions for the M4 evidence-workbench.

1. **Conflict/parked overlays are warn-only at G2a — no guard.** A `ChronologyRowOverlay` whose
   `base_hash_at_edit` no longer matches the rebuilt row is `CONFLICT` (base wins, both versions
   visible); an overlay whose encounter a merge absorbed is `PARKED_ORPHANED`. Neither **blocks** the
   G2a confirm: the counts surface in `evidence_review_vm` (`chronology.conflicts` / `chronology.parked`)
   for the attorney to see, but no transition guard reads them. The orchestrator owns any future
   decision to block on an unresolved conflict — the chronology module and the gate machine stay
   decoupled. *Rollback:* add a `no_overlay_conflicts` guard to the `G2A_CONFIRMED` edge in the
   orchestrator transition table (the count feed already exists in the VM builder).

2. **`need_more_records` leaves the flag OPEN → G2a proceeds via `requires_override` (D2).** It is a
   valid disposition, but it does NOT clear the flag — a flag dispositioned `need_more_records` still
   has a non-null `disposition`, so it is not "open" by the `disposition IS NULL` predicate, yet the
   real "we haven't resolved this" case (a HIGH flag the attorney has NOT dispositioned at all) is
   handled by the existing M0 guard `high_severity_dispositioned_or_override`: the G2a confirm over an
   open HIGH flag is `requires_override` (`409 override_required`), proceeded over with an audited
   reason. The risk engine does not special-case `need_more_records`; the guard's override path is the
   single "proceed eyes-open" mechanism. *Rollback:* if `need_more_records` must itself block the
   confirm, make it re-open the flag (set `disposition` back to NULL on that choice) so the existing
   open-HIGH guard catches it — no new guard needed.

3. **Intake-derived flags are the anchors-optional case (`low_property_damage`).** Every risk flag
   carries page anchors EXCEPT `low_property_damage`, which is derived purely from the attorney's G1.5
   `property_damage_estimate_cents` vs the threshold — there is no record page to cite, so its `anchors`
   are `[]` and its detail names the G1.5 field as its source. This is the one documented exception to
   the "every flag is anchored" reading of invariant 2; the LLM-labeled kinds are still page-anchored
   or rejected. *Rollback:* if intake facts ever get a synthetic anchor target (e.g. an intake-form
   document page), point `low_property_damage` at it and drop the exception.

4. **LLM label anchors are validated against the MATTER-WIDE page set (per-encounter precision
   deferred).** A risk label's `anchor_pages` are plain page ints, accepted iff every cited page number
   matches SOME valid `(document_id, page)` in the union of all encounter anchors + incident anchors; a
   page outside that set rejects the WHOLE label (counted `anchors_rejected`). The stored anchors become
   every valid `(document_id, page)` whose page number was cited — so when two documents share a page
   number, the flag anchors to both. Per-encounter anchor precision (disambiguating which document a
   shared page belongs to) is bounded by M4 and improves at S1/bbox time. *Rollback:* narrow the valid
   set to per-encounter pages once the labeler emits a per-encounter key (S1), tightening the anchor to
   the citing encounter's document.

5. **PHI disposition is DECOUPLED from risk-flag disposition (defense in depth, attorney-only).**
   Resolving a `third_party_phi` risk **flag** does NOT auto-clear the **exhibit's** `phi_disposition`
   — the two are separate acts. An open `third_party_phi` flag on a document forces/keeps the exhibit
   `pending` on pick, but clearing the exhibit into the binder is its own explicit **attorney-only**
   `set_phi_disposition` call (`PhiDispositionForbidden -> 403` for a non-attorney), and the manifest
   `blocking` preview lists a `pending` PHI on any entry that HAS includes. Two independent gates on
   someone else's medical data reaching the letter is deliberate. *Rollback:* couple them (auto-clear
   the exhibit when the flag is dispositioned) only if the double-gate proves to be friction with no
   safety benefit in the pilot.

6. **The wire carries a BARE `exhibit_token_id`; token-shaped strings never serialize.** The manifest
   read-model keeps the bracketed `[[EX_n]]` form internally, but the route serialization strips it to
   the bare id (`exhibit_token_id: "EX_1"`) — invariant 11's "nothing token-shaped reaches the wire",
   enforced by the wire scanner that runs on every response. Because the EX token is minted in the ONE
   shared per-matter ordinal namespace, its ordinal interleaves with FACT/AMT tokens (it is NOT `EX_1`
   in a full pipeline). The manifest entry also surfaces the Exhibit row id as `exhibit_id` so the
   workbench can drive `POST /api/exhibits/{id}/phi` (keyed by exhibit id) straight from the manifest
   view. *Rollback:* none needed — the bare-id rule is the invariant-11 floor; the `exhibit_id` field is
   additive.

7. **The per-kind cap is a PRESENTATION-layer bound, never suppression.** `settings.risk_flag_per_kind_cap`
   exists to bound UI display grouping; the risk engine **never drops a derived flag** — surfacing every
   adverse fact is invariant 6's whole point, and suppression is the one move this engine must not make.
   At M4 the setting is defined but **not yet read anywhere** (`run_risk_detectors` persists every
   candidate); the contract is that if it is ever applied it is a **display** bound at the view layer,
   never a filter in the engine. *Rollback:* none — if the cap ever needs to suppress at the engine, that
   is an invariant-6 change requiring a system-contract edit + ADR, not a config tweak.

8. **The chronology overlay edit vocabulary is CLOSED to four fields; DOS is the spine, not
   overridable.** `ChronologyOverlayRequest.edited_fields` accepts exactly `{narrative_override,
   provider_display, facility_display, encounter_type}`, all string-valued; an unknown key, a non-string
   value, or an empty dict (clearing is out of scope at M4) is a `422 invalid_edits`. The date of service
   is deliberately excluded: it orders every chronology row AND feeds the treatment-gap detector, so a
   wrong DOS is fixed by re-extraction, not a display overlay. *Rollback:* widen the closed set (or add
   a DOS-override path with a re-run of the gap detector) if a legitimate correction can only be made as
   an overlay — but that is a deliberate change to the spine contract, not a loosened validator.

## Consequences

- The G2a surface is end-to-end runnable and testable offline at M4 (the M4-exit E2E drives the full
  arc over HTTP: Phase 0 → G1 → G1.5 → analysis → paralegal prep → attorney disposition → manifest mint
  → G2a confirm → `plan_review` + registry freeze), and the `null`-provider path degrades visibly rather
  than stalling.
- Each decision names its later counterpart (an orchestrator conflict guard, `need_more_records`
  re-open, an intake anchor target, per-encounter anchor precision at S1, coupling PHI to the flag,
  widening the overlay vocabulary) so the deferral is traceable, not silent.
- The manifest is a **read-model preview only** — the M5 build gate reads its `blocking` list, but
  nothing here builds a PDF; the no-volunteer drafter constraint and the `undisposed_adverse` G3 block
  that consume G2a's dispositions are still M5.
- Role separation is server-enforced at two new attorney-only acts (HIGH-flag disposition, PHI
  disposition), both mapping a typed engine refusal to a `403`; the analysis run is authorized as a
  derived computation (any firm member), not a gate act.

## Alternatives Considered

- **Block G2a confirm on an unresolved overlay conflict** — rejected for M4: it couples the chronology
  module to the gate machine before there is a customer signal that a stale overlay must hard-stop the
  confirm; warn-only keeps the attorney informed without a new guard. *Rollback:* above (1).
- **Treat `need_more_records` as clearing the flag** — rejected: it would let "I still need records"
  silently satisfy the confirm guard, defeating the surface-always intent; leaving it open routes the
  case through the audited override. *Rollback:* above (2).
- **Require a page anchor on every flag, including `low_property_damage`** — rejected: an intake-derived
  amount comparison has no record page to cite; forcing a fake anchor would be dishonest provenance.
  *Rollback:* above (3).
- **Per-encounter anchor precision now** — rejected for M4: the labeler emits page ints, not a
  per-encounter key, so matter-wide validation is the honest bound until S1/bbox. *Rollback:* above (4).
- **Auto-clear the exhibit PHI when the risk flag is dispositioned** — rejected: collapsing the two
  removes the second, deliberate gate on third-party PHI reaching the binder. *Rollback:* above (5).
- **Serialize the full `[[EX_n]]` token on the wire** — rejected: it violates invariant 11's
  nothing-token-shaped-on-the-wire rule; the bare id carries the same information safely. *Rollback:*
  none (6).
- **Apply the per-kind cap in the engine** — rejected: suppressing a derived adverse fact violates
  invariant 6; the cap is a display concern. *Rollback:* none (7).
- **Allow a DOS override in the overlay vocabulary** — rejected: the DOS is the chronology spine and the
  gap detector's input; editing it as a display overlay would silently desync ordering and risk
  detection from the record. *Rollback:* above (8).
