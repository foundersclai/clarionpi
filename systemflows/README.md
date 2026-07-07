# System Flows

Editable Mermaid diagrams for the ClarionPI demand-package pipeline. They are
**architecture-facing, not marketing artifacts**: they show the as-built system —
attorney gates, source-of-truth boundaries, the token discipline, and where the
audit trail is written. The code is authoritative; when a diagram and the tree
disagree, fix the diagram (and if the *design* changed, record an ADR first —
see `docs/adr/`).

## The one-paragraph story

A paralegal uploads a records corpus; the system splits it into pages, classifies
and dedups it, and extracts facts. Every assertable fact becomes a **token**
(`[[FACT_n]] [[AMT_n]] [[CITE_n]] [[EX_n]]`) minted in a per-matter registry with
a source anchor (document + page). The attorney reviews facts, sets strategy,
disposes risk flags, and approves a plan; Brain-2 drafts the demand letter
**seeing only tokens, never raw facts**; a compliance panel re-verifies every
token against the registry and ledger; and the final package renders each token
back to its verified value — so every sentence in the letter is click-through
auditable to a source page, with a PHI audit row on every byte fetch.

## Diagrams

The `.md` files are the editable sources (Mermaid, rendered natively by
GitHub); `svg/` holds standalone renders of the same diagrams for decks,
docs, and anything that can't render Mermaid.

- [Matter lifecycle](matter_lifecycle.md) — the ten-state gate machine: five
  attorney gates, four system states, rework edges, and what a registry bump
  invalidates. **Start here.**
  ([forward path](svg/matter_lifecycle_forward.svg) ·
  [rework edges](svg/matter_lifecycle_rework.svg))
- [Intake & Phase 0](intake_phase0.md) — upload → pages/OCR → classify → dedup
  quarantine → extraction → registry sync → `corpus_ready`.
  ([svg](svg/intake_phase0.svg))
- [Fact registry & money](fact_registry_and_money.md) — how a page becomes a
  token, and how medical bills become an auditable specials ledger (integer
  cents, dedup exclusion *before* any sum).
  ([facts](svg/fact_registry_facts.svg) · [money](svg/fact_registry_money.svg))
- [Evidence review (G2a)](evidence_review_g2a.md) — the analysis run, risk
  flags, attorney dispositions, and the registry freeze that pins everything
  downstream. ([svg](svg/evidence_review_g2a.svg))
- [Demand generation (G2.5 → G3)](demand_generation.md) — plan approval,
  tokens-only drafting, deterministic validation, and the compliance panel
  (seven deterministic checks + a snapshot-locked judge).
  ([drafting](svg/demand_generation_drafting.svg) ·
  [compliance](svg/demand_generation_compliance.svg))
- [Package assembly](package_assembly.md) — byte-deterministic artifacts,
  continuous Bates stamping, the provenance report, and the immutable
  `ArtifactSet`. ([svg](svg/package_assembly.svg))
- [Provenance round-trip](provenance_roundtrip.md) — click a cited sentence,
  see the source page highlighted, leave an audit row (M6, ADR-0008).
  ([svg](svg/provenance_roundtrip.svg))

## Regenerating the SVGs

After editing a `.md` source, re-render its SVGs (mermaid-cli; the theme lives
in [mermaid-config.json](mermaid-config.json)):

```sh
npx -y @mermaid-js/mermaid-cli \
  -i systemflows/<flow>.md -o systemflows/svg/<flow>.svg \
  -b white -c systemflows/mermaid-config.json
```

`mmdc` emits one file per mermaid block (`<flow>-1.svg`, `<flow>-2.svg`);
rename multi-block outputs to the semantic names used above. Diagrams are
styled for light backgrounds (white canvas baked in).

## Diagram rules

- **Tokens never cross the wire.** Diagrams that touch the API show bare ids
  (`FACT_3`), never `[[FACT_3]]` — `wire_guard` enforces this in code; keep the
  diagrams honest about it.
- **Attorney gates are drawn as gates** (amber), system states as automatic
  (gray). If a step requires `role_attorney`, it must be visibly a gate.
- Vocabulary in node labels must match the enums in
  `backend/app/models/enums.py` (states, events, check kinds, SSE names) —
  don't paraphrase identifiers.
