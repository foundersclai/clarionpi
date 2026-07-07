# Demand Generation — Plan (G2.5), Tokens-Only Drafting, Compliance (G3)

The only place an LLM writes prose the client will send — so it is the most
constrained place in the system. Brain-2 drafts **against tokens**, its exact
prompt is snapshotted, and a compliance panel re-verifies everything before the
attorney sees a letter (`backend/app/engine/brain2/`,
`backend/app/engine/compliance/`).

## Plan → draft (G2.5 → drafting)

```mermaid
flowchart TB
    emit["Plan emit (explicit POST)<br/>skeleton from az.yaml letter_structure:<br/>intro_and_representation, liability,<br/>injuries_and_treatment,<br/>damages_and_specials, demand_and_deadline"]:::auto
    alloc["Deterministic token allocator<br/>each section gets its permitted<br/>FACT/AMT/CITE/EX tokens"]:::auto
    g25["plan_review — G2.5<br/>attorney edits emphasis + structure,<br/>approves; approval binds plan version<br/>to the FROZEN registry version"]:::gate
    memo["Strategy memo — Opus<br/>(draft.memo stage)"]:::llm
    draft["Section drafter — Opus, tokens only<br/>layered prompt: rules_blocks -><br/>matter_directives -><br/>final_hard_constraints LAST<br/>DrafterPromptSnapshot (input_hash)"]:::llm
    val["Deterministic validator<br/>tokens ⊆ allocation, structure, lengths<br/>strict single retry -> surfaced_failed<br/>(never silently accepted)"]:::guard
    render["Renderer<br/>resolves tokens via the registry,<br/>emits char-offset spans<br/>(span_id, start, end, token_id)"]:::auto

    emit --> alloc --> g25 --> memo --> draft --> val --> render
    val -. "retry once w/ violations" .-> draft

    classDef auto fill:#eceff1,stroke:#78909c,color:#333
    classDef gate fill:#fff3cd,stroke:#b58900,stroke-width:2px,color:#333
    classDef llm fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#333
    classDef guard fill:#fdecea,stroke:#c62828,stroke-width:2px,color:#333
```

## Compliance pass → G3

```mermaid
flowchart TB
    pass["Compliance pass<br/>(precondition: draft registry version<br/>== live version, else DraftRegistryDrift)"]:::auto

    subgraph det["Seven deterministic checks"]
        d1["orphan_token · amt_ledger_mismatch<br/>(live hash) · dead_anchor (page bounds)<br/>· missing_exhibit · missing_statutory_term<br/>· undisposed_adverse · prose_total_mismatch"]:::guard
    end

    judge["Per-section judge — Sonnet<br/>on the EXACT persisted prompt snapshot<br/>(hash mismatch -> SnapshotDrift)<br/>kinds: unsupported_causation,<br/>strategy_drift, tone<br/>double parse failure -> fail-visible finding"]:::llm
    findings["Findings — all BLOCKING in v1<br/>lifecycle: open -> patched/regenerated<br/>-> re_verified -> dispositioned"]:::registry
    mech["mechanical bucket -> span-patch<br/>(deterministic re-render;<br/>validation failure ESCALATES to regen)"]:::auto
    sem["semantic bucket -> regen<br/>fix-instructions ride retry_violations<br/>(snapshot-neutral) -> back to drafting"]:::auto
    hard["HARD BLOCKS — never shippable:<br/>orphan_token, amt_ledger_mismatch,<br/>dead_anchor, missing_exhibit,<br/>undisposed_adverse<br/>(short-circuit the judge)"]:::guard
    g3["compliance_review — G3<br/>attorney reads letter w/ clickable spans,<br/>disposes findings, approves<br/>[no_blocking_findings guard]"]:::gate

    pass --> det --> judge --> findings
    findings --> mech
    findings --> sem
    det -. "hard block found" .-> hard
    mech -- "patched -> re_verified" --> g3
    sem -- "semantic_finding_regen<br/>-> back to drafting<br/>(new draft version, new pass)" --> redraft["drafting"]:::auto
    redraft -.-> pass
    g3 --> out["package_assembly"]:::auto

    classDef auto fill:#eceff1,stroke:#78909c,color:#333
    classDef gate fill:#fff3cd,stroke:#b58900,stroke-width:2px,color:#333
    classDef llm fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#333
    classDef guard fill:#fdecea,stroke:#c62828,stroke-width:2px,color:#333
    classDef registry fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#333
```

## The two symmetry locks

- **Drafter ↔ judge:** the judge evaluates the *persisted* `DrafterPromptSnapshot`
  (matched by `input_hash`), so it judges exactly what the drafter was told —
  a drifted prompt is a `SnapshotDrift` error, not a silently wrong verdict.
- **Mechanical ↔ semantic routing is conservative:** only the four enumerated
  mechanical kinds (`amt_ledger_mismatch`, `missing_exhibit`,
  `missing_statutory_term`, `prose_total_mismatch`) are span-patch-routable;
  every other kind defaults to regeneration (`compliance/engine.py::bucket_for`).

## What the attorney sees

Sections stream over SSE `section` events as they validate; the G3 panel shows
the rendered letter with every cited span clickable (the spans emitted by the
renderer power the [provenance round-trip](provenance_roundtrip.md)). Draft
lifecycle: `drafting → validated → in_compliance → approved`; a re-draft after
drift is a **new version**, never an overwrite (`superseded`).
