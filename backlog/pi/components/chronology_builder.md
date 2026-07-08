# Component — chronology_builder

- **Status:** DRAFT for founder review · **Date:** 2026-07-04
- **Planned module path:** `app/engine/brain1/chronology`
- **Contract doc (M0):** `docs/module_contracts/app.engine.brain1.chronology.md`
- Refines [04 §2](../04_data_model_and_contracts.md); does not contradict it. Level-1 context:
  [01 §3 pipeline](../01_high_level_design.md), [01 §1 invariants](../01_high_level_design.md).

## 1. Responsibility

Turn merged `MedicalEncounter` rows into **the medical chronology**: a deterministic table
(one row per encounter) plus a per-encounter tokenized narrative, editable by the paralegal
at G2a through a first-class, logged **overlay** that survives rebuilds. Produces the
chronology grid for `api_and_wire` and the chronology appendix input for `package_builder`.

**NOT responsible for:** encounter extraction or dedup/merge (`corpus_extraction` owns that);
deciding which encounters/exhibits ship in the letter (G2a picks, attorney judgment);
any arithmetic (`money_engine`); minting tokens (`fact_registry`).

## 2. Boundary

| Direction | What | Peer component |
|---|---|---|
| consumes | `MedicalEncounter[]` (merged, anchored) | corpus_extraction.md |
| consumes | `[[FACT_n]]` display forms + resolution | fact_registry.md |
| owns | `ChronologyRowOverlay` (paralegal edits, keyed by `encounter_id`) | — |
| produces | chronology view rows (rendered, no tokens) | api_and_wire.md |
| produces | chronology appendix input (rows + narratives) | package_builder.md |
| coordinated by | `analysis_running` run start, G2a rebuild triggers | orchestrator_gates.md |

## 3. Key types & fields

```python
class ChronologyRow:                       # derived — always rebuildable (inv. 10)
    row_id: str                            # == encounter_id (stable across rebuilds)
    date_of_service: date; provider_display: str; facility_display: str
    episode_key: str                       # provider-episode group key (sort/group only)
    narrative_tokenized: str               # Sonnet; [[FACT_n]] refs only (inv. 5)
    anchors: list[PageAnchor]              # ≥1, inherited from the encounter (inv. 2)
    base_hash: str                         # of base row inputs — overlay-conflict detector

class ChronologyRowOverlay:                # owned here; human election, stored separately
    matter_id: UUID; encounter_id: str     # key
    edited_fields: dict[str, JsonValue]    # e.g. narrative override, provider display fix
    base_hash_at_edit: str                 # base_hash the paralegal edited against
    actor_id: UUID; created_at: datetime
    status: Literal["applied","parked_orphaned","conflict"]  # never silently dropped
```

Overlays live in a distinct store from the derived `ChronologyRow`s (invariant 10): rebuild
regenerates base rows from encounters; the overlay layer reapplies on top by `encounter_id`.

## 4. Internal design

- **Deterministic assembly (pure code):** sort by `date_of_service`, then group by provider
  episode; `row_id = encounter_id` so rows are stable across rebuilds and overlays re-key
  cleanly. No LLM in row identity or ordering.
- **Narrative generation (Sonnet, per-encounter only):** each narrative is generated in
  isolation and may reference **only** `[[FACT_n]]` tokens already in the registry — never
  raw names/dates/amounts (invariant 5). Regen is always scoped to one encounter; there is
  **no whole-chronology regen** path (bounds cost + blast radius; TM per-section-regen lesson).
- **Overlay reapply + conflict surfacing:** on rebuild, for each overlay compare
  `base_hash_at_edit` to the fresh `base_hash`. Match → apply. Differ → mark `conflict` and
  surface both versions at G2a; **never auto-resolve** (invariant 10 — humans see the diff).
- **Orphan handling:** if an `encounter_id` vanishes after a re-merge, its overlay is parked
  (`parked_orphaned`) and flagged, not deleted — the paralegal's work is recoverable.
- **Tokens-only build check:** every narrative passes a Tier-1 "zero unregistered claims"
  scan (every `[[FACT_n]]` resolves at the current registry version) before the row is
  emitted; a failure fails the build, it does not ship a sentence.

## 5. Invariants enforced

- **2** — every emitted row (and its narrative) carries ≥1 `(doc, page)` anchor.
- **5** — narratives are tokens-only; raw provider/date/amount never leave the generator.
- **10** — base rows are rebuildable from encounters; the overlay is a separate, first-class
  store, reapplied deterministically, conflicts surfaced not merged.

## 6. Failure modes & handling

| Failure | Detection | Handling |
|---|---|---|
| Overlay orphaned by re-merge (encounter_id gone) | `encounter_id` absent post-rebuild | Park overlay `parked_orphaned` + flag at G2a; never delete |
| Overlay conflicts with changed base | `base_hash_at_edit ≠ base_hash` | Mark `conflict`, surface both versions at G2a |
| Narrative cites unregistered claim | Tier-1 zero-unregistered-claims scan | Fail the build for that row; retry generation; never emit |
| Duplicate `date_of_service`/provider collision | Stable sort tiebreak on `encounter_id` | Deterministic order; no random reflow |
| Missing display form for a `[[FACT_n]]` | Registry resolution miss | Sentinel + log (never token on wire); block emit |

## 7. Test strategy

- **Rebuild + overlay idempotence:** rebuild N times over a fixture matter → identical base
  rows; overlay reapplies to the same result; `row_id` stability asserted.
- **Zero-unregistered-claims** on live-case fixtures (per refusal-analog scenarios): every
  narrative token resolves; planted unregistered mention fails the build.
- **Row ordering stability:** shuffled encounter input → identical ordered output; episode
  grouping golden-tested.
- **Conflict/orphan surfacing:** mutate/remove an encounter post-edit → overlay lands in the
  correct `conflict` / `parked_orphaned` state, never silently applied or dropped.

## 8. Open questions

1. Episode-key definition: same provider + rolling gap window, or explicit `encounter_type`
   transitions? (affects grouping only, not row identity — safe to defer to fixtures.)
2. Should a `conflict` overlay block G2a confirm, or only warn? (Leaning warn — orchestrator
   gate owns the block decision; see orchestrator_gates.md invalidation matrix.)
3. xlsx column set for the chronology export — owned by `package_builder`; this doc supplies
   rows + narratives, not layout.
