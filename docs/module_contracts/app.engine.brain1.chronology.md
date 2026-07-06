# app.engine.brain1.chronology

Backs [`system_contract.md`](../system_contract.md) invariants **2, 5, 10**.
Module path: `backend/app/engine/brain1`.
Design source: [`backlog/pi/components/chronology_builder.md`](../../backlog/pi/components/chronology_builder.md).

## Status

**Live @ M2.** The chronology builder is implemented and tested under
`backend/app/engine/brain1/chronology.py`:

- `build_chronology` — the derived-row rebuild: loads the matter's encounters, generates any
  missing narratives (per-encounter, isolated), reapplies overlays, and runs the
  zero-unregistered-claims scan.
- `base_hash_for` — the SHA-256 over an encounter's base inputs that drives overlay
  conflict detection.
- `upsert_overlay` — create/update a paralegal's `ChronologyRowOverlay` (edits replaced
  wholesale; `base_hash_at_edit` pinned; audit event written).
- `render_rows_for_wire` — the view-layer helper that detokenizes narratives through the
  registry so nothing token-shaped reaches the wire.

Narrative generation composes `app.core.llm_telemetry.MeteredLLMClient` (the single metered
door) and `app.engine.tokenizer.registry` (resolution). The G2a overlay-editing UI lands M4;
this wave ships the store + build semantics it will drive.

### M2 boundaries

- **Rows are derived, never persisted (inv 10).** A `ChronologyRow` is an in-memory value
  object rebuilt from the encounters on every `build_chronology` call. `row_id ==
  str(encounter_id)` is stable across rebuilds so overlays re-key cleanly — the id never
  reflows. Order is `(date_of_service, created_at, id)`: a stable tiebreak, so same-day
  encounters never randomly reorder between builds.
- **Narratives are persisted on `MedicalEncounter.narrative_tokenized`, and this module is the
  DOCUMENTED SINGLE WRITER of that column.** No other module writes it. Generation is
  **per-encounter and isolated** (never a whole-chronology regen), committed per encounter; a
  non-empty narrative is never regenerated.
- **Narrative generation is tokens-only (inv 5), gated deterministically (inv 13).** The
  generator prompt hands the model the encounter's `[[FACT_n]]` token id + its display form +
  the encounter's clinical lists (the content it may summarize); the raw provider name and date
  render *from the token* and are never restated. The result passes a deterministic GATE — own
  token present ≥1, every token resolves, no raw provider string, no ISO date — with **exactly
  one** regeneration naming the violation on failure (the house one-parse-retry and the
  validation-regeneration collapse into that single second attempt), then the narrative is left
  empty and counted `narratives_failed`. The gate never edits the narrative.
- **Overlays are never silently dropped; conflicts/parks are never auto-resolved
  (chronology_builder §3).** On rebuild an overlay whose `base_hash_at_edit` matches the fresh
  `base_hash_for` is `APPLIED` (its `edited_fields` laid over the base in `effective_fields`); a
  mismatch is `CONFLICT` (base wins in the row, the edit stays visible on the overlay row for
  G2a); an overlay whose encounter is gone (a merge absorbed it) is `PARKED_ORPHANED` — parked,
  never deleted, because paralegal work is recoverable.
- **Zero-unregistered-claims is the M2 exit criterion, RETURNED not raised.** `build_chronology`
  scans every row's narrative and collects unresolved tokens into
  `ChronologyBuildOutcome.unregistered_claims` (ERROR-logged per token). A healthy build has it
  empty; the eval asserts empty; G3 blocks on it downstream — the build itself does not raise.
- **No arithmetic, no minting.** The chronology never sums money and never mints a token; a
  missing FACT token (registry sync has not run for an encounter) is a visible narrative skip,
  not a mint.

## Responsibility

Turn the matter's already-extracted, already-tokenized `MedicalEncounter` rows into the
attorney-review **chronology**: an ordered, deterministic set of derived rows, each with a
tokens-only narrative, over which a paralegal's row edits (**overlays**) are laid, quarantined,
or parked but never lost. It owns the overlay store and the single-writer relationship to the
encounter narrative column, and it is the producer of the chronology surface the appendix /
package builder and the G2a review UI consume.

**Not responsible for:** *extracting* encounters (`app.corpus.extraction`); *minting or
resolving* tokens (`app.engine.tokenizer` — the chronology consumes resolution, it never mints);
any **arithmetic** (`app.money.ledger` owns cents; narratives may state no dollar figure);
deciding *what enters the letter* (attorney gates); the demand prose itself (`app.engine.brain2`).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `ChronologyRowOverlay` (the paralegal row-edit store) | — |
| Owns | `MedicalEncounter.narrative_tokenized` (sole writer of that column) | — |
| Consumes | merged/tokenized `MedicalEncounter` rows | app.corpus.extraction |
| Consumes | token → display form (prompt-safe) + `scan_unregistered` + wire resolution | app.engine.tokenizer |
| Consumes | metered narrative generation (stage `chronology.narrative`) | app.core.llm_telemetry |
| Consumes | the actor (paralegal) for an overlay upsert | app.engine.orchestrator (G2a) |
| Produces | derived chronology rows (`ChronologyRow[]`) + build accounting | app.package.builder / appendix |
| Produces | detokenized wire rows (`render_rows_for_wire`) | app.api.view_models → frontend |
| Produces | `chronology_overlay_upserted` audit events | app.core.audit |

## Invariants enforced

- **[2]** Rows carry the encounter's anchors verbatim; the narrative's tokens resolve through the
  registry (which runs anchor integrity), so a row whose narrative cites an unresolved slot is a
  returned `unregistered_claims` finding, not a wired guess.
- **[5]** Narrative generation is tokens-only: the generator sees display forms + token ids, never
  raw names/dates/amounts to restate; the deterministic gate rejects a restated provider name or
  date and any token that does not resolve; `render_rows_for_wire` detokenizes so nothing
  token-shaped reaches the wire.
- **[10]** The chronology is derived state — rows are rebuilt from encounters every time, never
  persisted; `row_id == str(encounter_id)` keeps overlays addressable across rebuilds; overlays
  survive rebuilds and are reconciled by `base_hash`, never silently dropped.

## Vocabulary

`ChronologyRow` (derived, never persisted; `row_id == str(encounter_id)`; `base_hash`;
`overlay_status`; `effective_fields` = base with an `APPLIED` overlay laid over) ·
`ChronologyBuildOutcome` (`narratives_generated`/`_skipped`/`_failed`,
`overlays_applied`/`_conflict`/`_parked`, `unregistered_claims`) · `base_hash_for` (SHA-256 over
`(dos iso, provider, facility, encounter_type, complaints, findings, diagnoses, procedures,
work_status, narrative_tokenized)`) · `OverlayStatus` ∈ {`applied`, `parked_orphaned`,
`conflict`} — **never auto-resolved** · narrative stage id `chronology.narrative` · audit kind
`chronology_overlay_upserted` · **single regeneration** per narrative.

## Change rule

A boundary change requiring a contract update: changing the `ChronologyRow` /
`ChronologyBuildOutcome` shape or the `row_id == encounter_id` stability rule; changing the
`base_hash_for` input tuple (it is the overlay-conflict contract); changing the overlay
apply/conflict/park semantics or the never-auto-resolve rule; changing the narrative validation
gates, the tokens-only rule, or the single-regeneration budget; changing the **single-writer
exception** on `MedicalEncounter.narrative_tokenized` (chronology is the one writer — a write
elsewhere is a boundary breach); changing the `chronology.narrative` stage id or the
`chronology_overlay_upserted` audit kind. Update this file **and**
[`system_contract.md`](../system_contract.md) §2/5/10 in the same PR.
