# Package Assembly — Byte-Deterministic Artifacts

G3 approval triggers an automatic build of the deliverable set. The build is
**byte-deterministic** — same matter, same draft version, same registry version
produce sha256-identical files — so "which exact package did we send" is always
answerable (`backend/app/package/`).

```mermaid
flowchart TB
    g3["g3_approved -> package_assembly"]:::auto
    man["Draft manifest — manifest.py<br/>exhibits + integrity verdicts,<br/>EX tokens minted via the registry,<br/>bare ids on the wire"]:::auto
    blocked["BinderBlocked<br/>pending PHI disposition or<br/>failed integrity verdict<br/>-> build refuses, gate shows why"]:::guard

    subgraph artifacts["Artifacts (each token-leak-scanned before write)"]
        docx["Demand letter .docx<br/>every token rendered to its<br/>verified value — token-free text"]:::artifact
        xlsx["Chronology .xlsx<br/>fixed metadata"]:::artifact
        binder["Binder .pdf<br/>unstamped index page, then<br/>continuous Bates: CP00001, CP00002...<br/>excluded pages honored"]:::artifact
        prov["Provenance report<br/>1) every rendered fact + source anchor<br/>(completeness property)<br/>2) adverse facts omitted + rationale<br/>3) judgment calls"]:::artifact
    end

    set["ArtifactSet — IMMUTABLE<br/>keyed (matter, draft_version,<br/>registry_version)<br/>rebuild returns the SAME set (reused)"]:::terminal
    dl["Attorney downloads<br/>every download audited"]:::gate

    g3 --> man
    man -. "verdict fails" .-> blocked
    man --> artifacts --> set
    set -- "artifacts_built -> package_ready" --> dl

    classDef auto fill:#eceff1,stroke:#78909c,color:#333
    classDef gate fill:#fff3cd,stroke:#b58900,stroke-width:2px,color:#333
    classDef guard fill:#fdecea,stroke:#c62828,stroke-width:2px,color:#333
    classDef artifact fill:#e8f5e9,stroke:#2e7d32,color:#333
    classDef terminal fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#333
```

## How byte-determinism is achieved

- reportlab `invariant=1`, a pinned pypdf writer `_ID`, and fixed docx/xlsx
  metadata (no timestamps, no random UUIDs) — the whole set hashes stably.
- Storage keys are versioned: `matters/{id}/artifacts/v{draft}.{registry}/...` —
  a new draft or registry version is a **new keyspace**, never an overwrite.

## Business meaning

- `package_ready` is terminal: a `registry_bumped` there is *refused* — late
  records start a new draft cycle rather than mutating a package that may
  already be in an adjuster's inbox.
- The provenance report is the malpractice-defense artifact: it proves every
  asserted fact traces to a source page, and that adverse facts were seen and
  weighed (the attorney's G2a disposition reasons), not missed.
- The letter itself contains **no tokens and no sentinels** — an
  `ArtifactTokenLeak` or an unresolved fact fails the build loudly instead of
  shipping a placeholder to an adjuster.
