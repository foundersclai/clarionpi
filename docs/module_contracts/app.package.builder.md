# app.package.builder

Backs [`system_contract.md`](../system_contract.md) invariants **2, 10, 11**.
Module path: `backend/app/package`.
Design source: [`backlog/pi/components/package_builder.md`](../../backlog/pi/components/package_builder.md).

## Status

**Stub @ M0, lands M5.** `app/package` is a package stub (empty `__init__.py`).
The `ArtifactKind` enum is in `app/models`. No docx/pdf/xlsx build, Bates, manifest,
or provenance-report logic exists yet.

## Responsibility

Turn an **approved draft + G2a picks** into the deliverable artifacts, all
derivable purely from approved state:

- **`letter.docx`** (python-docx) — firm letterhead slot; body is the **rendered**
  text from `app.engine.tokenizer` resolution: **zero tokens survive into the
  artifact** — a token reaching the docx is a **build failure**.
- **exhibit binder `.pdf`** — collation in manifest order, a bookmark per exhibit,
  a generated index page, **Bates stamping**, page-level include/exclude from the
  G2a picks.
- **`chronology.xlsx`** — the chronology rows + narratives, exported.
- **provenance report** — the per-demand audit artifact: every rendered fact →
  source doc/page + verification status + source (the positioning artifact, MVP).

Redaction v1 = **page-level exclusion** + a `third_party_phi` disposition gate
(undispositioned third-party-PHI pages **block the build**).

**Not responsible for:** *what* is included (G2a picks + G3 approval decide);
prose content (`app.engine.brain2`); minting/resolving tokens beyond calling
`app.engine.tokenizer` resolution; sending packages to carriers (out of scope v1).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `BinderManifest`, `ArtifactSet`, Bates numbering, provenance report | — |
| Consumes | approved `DEMAND_DRAFT` + `DraftSection[]` | app.engine.brain2 · app.engine.compliance |
| Consumes | token → value + anchors (render resolution) | app.engine.tokenizer |
| Consumes | ledger totals (letter + xlsx figures) | app.money.ledger |
| Consumes | exhibit picks + page include/exclude + PHI dispositions | frontend (G2a) · brain1 (risk) |
| Consumes | object-store put/get; presign delegated | app/core · app.api.view_models |
| Produces | `letter.docx`, `binder.pdf`, `chronology.xlsx`, `provenance_report.pdf` + SHA-256 manifest | app.api.view_models (presigned downloads) |
| Produces | binder manifest (EX existence) for the G3 check | app.engine.compliance |

## Invariants enforced

- **[2]** The provenance report is the per-demand proof that **100% of rendered
  facts** resolve to a live `(doc, page)` anchor; a rendered fact absent from the
  report is a build error. A missing/superseded exhibit page **fails the build**
  (build-time integrity pre-check, not a silent gap at delivery).
- **[10]** Every artifact is derivable purely from approved state, keyed by
  `(draft_version, registry_version)` — a rebuild after drift is a **new**
  `ArtifactSet`, never an overwrite; nothing in a package is hand-authored.
- **[11]** A **post-render scan asserts no token-shaped string survives** into the
  docx/pdf; a surviving token fails the build and logs the orphan id loudly.

## Vocabulary

`BinderManifest` (ordered `ExhibitEntry` — collation order == index order; Bates
pinned to `version`; `phi_disposition` ∈ {`cleared`, `excluded`, `pending`}) ·
`ArtifactSet` (keyed by `(draft_version, registry_version)`; each `Artifact` has
`kind`, `object_key`, `sha256`, `byte_count`) · `ArtifactKind`
(`letter_docx`/`binder_pdf`/`chronology_xlsx`/`provenance_report`) · **deterministic
bytes** (same inputs → hash-matching artifacts; the golden-artifact test bar) ·
**redaction gate** (`pending` blocks the build).

## Change rule

A boundary change requiring a contract update: adding/removing an artifact kind or
changing the `ArtifactSet` keying; changing the no-token-survives build invariant,
the Bates-pinning rule, or the deterministic-bytes contract; changing the
provenance-report completeness property or the redaction-disposition gate;
changing the binder-manifest shape the G3 check consumes. Update this file **and**
[`system_contract.md`](../system_contract.md) §2/10/11 in the same PR.
