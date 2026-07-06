"""Brain-1 assembly surfaces (chronology_builder / system_contract §2, 5, 10).

Brain-1 turns the matter's *already-extracted, already-tokenized* facts into the
attorney-review surfaces that precede drafting. Wave C1 ships the **chronology builder**:

* a DERIVED chronology — one row per :class:`~app.models.orm.MedicalEncounter`, rebuilt from
  the encounters every time, never persisted (inv 10);
* a first-class **overlay store** (:class:`~app.models.orm.ChronologyRowOverlay`) so a
  paralegal's row edit survives rebuilds — laid over the base on a hash match, quarantined as
  ``CONFLICT`` on drift, and ``PARKED_ORPHANED`` when a merge absorbs its encounter, but never
  silently dropped;
* **tokens-only narratives** (inv 5): a per-encounter generator that refers to a visit only by
  its registry ``[[FACT_n]]`` token — the raw provider name and date render from the token, they
  are never restated in the prose — validated by a deterministic gate before it persists.

The chronology does **no arithmetic** and **mints no tokens** — it composes the landed
tokenizer (:mod:`app.engine.tokenizer.registry`) for resolution and the metered client
(:class:`app.core.llm_telemetry.MeteredLLMClient`) for narrative generation.

The public surface lives in :mod:`app.engine.brain1.chronology`:

* :class:`~app.engine.brain1.chronology.ChronologyRow` (derived; ``row_id == str(encounter_id)``),
  :class:`~app.engine.brain1.chronology.ChronologyBuildOutcome`;
* :func:`~app.engine.brain1.chronology.build_chronology` (rows + narratives + overlay reapply),
  :func:`~app.engine.brain1.chronology.base_hash_for` (the overlay-conflict detector);
* :func:`~app.engine.brain1.chronology.upsert_overlay` (create/update a row edit),
  :func:`~app.engine.brain1.chronology.render_rows_for_wire` (detokenized, wire-safe rows).

Single-writer note: chronology is the **one documented writer** of
``MedicalEncounter.narrative_tokenized`` — it fills that column and nothing else does.
"""

from __future__ import annotations
