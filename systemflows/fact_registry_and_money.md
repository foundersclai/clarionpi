# Fact Registry & Money — from Page to Token to Ledger

The registry is the system's spine: every assertable fact becomes a **token**
with a source anchor, and every dollar figure is re-derivable from source rows.
Brain-2 later drafts *against tokens only* — it never sees a raw name, date, or
amount it could distort (`backend/app/engine/tokenizer/registry.py`,
`backend/app/money/`).

## Facts: page → token

```mermaid
flowchart TB
    page["DocumentPage<br/>(immutable identity,<br/>active text version)"]
    win["Windowed extractors<br/>parties / encounters / billing lines<br/>anchor-in-window REJECTION:<br/>a fact citing a page outside its<br/>window is dropped, not kept"]:::auto
    merge["Deterministic-key merge<br/>+ LLM tiebreak (skip-not-guess<br/>when offline) — reversible"]:::auto
    mint["Registry mint / sync<br/>one shared ordinal namespace:<br/>FACT_n, AMT_n, CITE_n, EX_n<br/>idempotent by source_ref"]:::registry
    anchor["Every token carries anchors<br/>(document_id, page)<br/>+ verify status"]:::registry
    bump["Version bump w/ reason:<br/>extraction_sync, ledger_sync,<br/>attorney_fact, exhibit_sync"]:::registry

    page --> win --> merge --> mint --> anchor
    mint --> bump
    bump -. "registry_bumped event<br/>-> invalidation matrix" .-> out["(see matter_lifecycle)"]

    classDef auto fill:#eceff1,stroke:#78909c,color:#333
    classDef registry fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#333
```

## Money: bills → specials ledger → AMT tokens

```mermaid
flowchart TB
    lines["Billing lines from extraction<br/>(integer CENTS everywhere —<br/>no floats, ever)"]
    excl["Document-level dedup exclusion<br/>BEFORE any sum:<br/>a duplicate bill can never<br/>inflate the demand"]:::guard
    basis["billed | paid basis<br/>from az.yaml rules row<br/>(Lopez cite; verify_status =<br/>unverified pending counsel)"]:::auto
    ledger["SpecialsLedger — derived view<br/>categories: er, ambulance, imaging,<br/>pt_chiro, ortho, surgery, pharmacy, other<br/>line_set_hash over source rows"]:::registry
    amt["AMT tokens minted per total<br/>ledger_hash pinned at mint"]:::registry
    verify["Any later render RE-VERIFIES<br/>live ledger hash vs pinned hash<br/>mismatch -> amt_mismatch finding<br/>(hard block at G3)"]:::guard

    lines --> excl --> basis --> ledger --> amt --> verify

    classDef auto fill:#eceff1,stroke:#78909c,color:#333
    classDef registry fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#333
    classDef guard fill:#fdecea,stroke:#c62828,stroke-width:2px,color:#333
```

## Token render outcomes (what downstream consumers get)

`resolve_for_render` returns one outcome per token — the letter renderer, the
compliance checks, and the provenance endpoint all speak this vocabulary:

| Outcome | Meaning | Downstream effect |
| --- | --- | --- |
| `ok` | resolves at the current registry version | renders the display form |
| `orphan` | token not in the registry | renders the sentinel `[UNRESOLVED FACT]` (deliberately not token-shaped) + `orphan_token` hard block |
| `amt_mismatch` | ledger changed since mint | `amt_ledger_mismatch` hard block |
| `unverified` / `disputed` | fact exists but isn't attorney-verified / is contested | surfaced at gates; the letter can't ship around a hard block |

## Wire discipline

Nothing token-shaped ever serializes: the API sends bare ids (`FACT_3`), and
`wire_guard.scan_wire_payload` scans every response — a leak **raises in dev**
(500) and scrubs-to-sentinel + logs in prod. Attorney edits at G1 land as
`attorney_fact` registry entries with their own version bump, never as silent
mutations. Chronology narratives are built tokens-only and rendered through the
same resolver (`app/engine/brain1/`).
