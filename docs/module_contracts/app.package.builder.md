# app.package.builder

Backs [`system_contract.md`](../system_contract.md) invariants **2, 10, 11**.
Module path: `backend/app/package`.
Design source: [`backlog/pi/components/package_builder.md`](../../backlog/pi/components/package_builder.md).

## Status

**Live @ M5 (all four artifacts build).** `app/package/manifest.py` (the M4 read-model
below) plus the M5 builders: `artifacts.py` (`letter.docx` + `chronology.xlsx`),
`binder.py` (the exhibit `binder.pdf` — collation + continuous Bates + index + bookmarks),
`provenance.py` (the `provenance_report.pdf` audit trail), and `build.py` (the `ArtifactSet`
orchestration — build all four, gate, store, record, immutably). Wired on the wire by
`app/api/routes/drafting.py` (the `package/build` SSE run + the list + the byte download).
The M5-exit E2E (`tests/api/test_m5_exit_flow.py`) builds + downloads all four over HTTP.
Decisions recorded in [ADR-0007](../adr/0007-m5-drafting-decisions.md).

### M5 build (the four artifacts)

- **`build_artifact_set` is atomic + immutable, keyed by `(matter, draft_version,
  registry_version)`.** An existing set for that triple is returned `reused=True` (a rebuild
  after drift is a NEW set under new versions, never an overwrite); otherwise all four
  artifacts build (and the binder gate passes) BEFORE any row is written — a `BinderBlocked`
  / `ArtifactTokenLeak` / `BinderPageMissing` propagates with nothing persisted. It does NOT
  transition the gate (the route owns the `ARTIFACTS_BUILT` advance). Each artifact stores
  under `matters/{id}/artifacts/v{draft_version}.{registry_version}/{filename}`; the row's
  `artifacts` JSON is `[{kind, object_key, sha256, byte_count}]`.
- **Byte-determinism (inv 10) — sha-stable across builds, asserted.** No wall-clock enters the
  bytes: docx/xlsx pin their core/workbook properties to a fixed timestamp; the binder + the
  provenance report draw with reportlab `invariant=1` (no wall-clock CreationDate, no random
  id) and the binder pins the pypdf metadata dates + a fixed 16-byte file `/ID`. The
  `ArtifactSet.created_at` is a separate DB default, deliberately NOT in the bytes.
- **The binder: continuous Bates + blocking gate + integrity double-check.** A non-empty
  manifest `blocking` list raises `BinderBlocked` (the M5 build gate — pending PHI / non-`ok`
  integrity never ships). Bates are continuous `f"{prefix}{n:05d}"` starting `00001` AFTER the
  unstamped index page, in manifest order, deterministically; the prefix is
  `settings.bates_prefix` (`"CP"`). An included page beyond the SOURCE PDF's real page count (a
  re-ingest/corruption mismatch the stored `page_count` missed) raises `BinderPageMissing`. One
  bookmark per exhibit at its first collated page; the index page lists each exhibit's bare
  token id + filename + Bates range.
- **The letter: rendered previews only; the memo is excluded.** `build_letter_docx` writes a
  generated letterhead (firm-name heading + rule — template ingestion is a recorded open
  question) + a `Re:` line + one heading-and-body per PASSED section (the `rendered_preview`,
  never the tokenized body). Every paragraph is token-scanned (`ArtifactTokenLeak` on a
  survivor). The `memo` is accepted for a stable signature but NEVER written into the letter (an
  attorney artifact, never sent to the carrier — ADR-0007).
- **The provenance report (inv 2) is re-runnable + complete.** `build_provenance_report` reads
  the DB but writes no rows (the M6 export seam): Part 1 walks each section's spans and resolves
  each token live (display form + outcome + source doc/page anchors) — the **completeness
  property**: exactly one fact entry per rendered span across the sections (asserted in the
  E2E); Part 2 is the `omit_with_rationale` adverse trail + `need_more_records` open items; Part
  3 is the OVERRIDE-dispositioned findings (the judgment-call log). Every rendered string is
  token-scanned.
- **The download route surfaces the kind-keyed url, never the `object_key`, and audits.** The
  list (`artifact_sets_view`) and the byte download (`get_artifact_download`) expose
  `{kind, sha256, byte_count, url}` — the `object_key` is INTERNAL. The download serves the bytes
  with the per-kind media type + a `Content-Disposition` filename, writes an `artifact_downloaded`
  audit, and is NOT wire-scanned (binary bytes are not a token surface; the build already scanned
  them). A cross-firm matter / an unknown kind → `404 artifact_not_found`.

Below is the M4 manifest read-model this build consumes.

`app/package/manifest.py` is implemented and tested — the *preview* of the M5 exhibit binder:

- `upsert_exhibit_pick` — the per-document page pick (tri-state `include_pages` / `excluded_pages`;
  both sorted + deduped; typed `InvalidPick` 422 for a foreign doc / include-exclude overlap /
  out-of-range page). An undispositioned `third_party_phi` flag on the doc forces/keeps `pending`.
- `set_phi_disposition` — the **attorney-only** third-party-PHI disposition (typed
  `PhiDispositionForbidden` → 403 for a non-attorney).
- `build_draft_manifest` — the ordered `(sort_order, filename, document_id)` integrity-checked
  manifest read-model; `mint_tokens=True` mints the `[[EX_n]]` tokens through the registry (the only
  minter), idempotently.

The docx/pdf/xlsx build, Bates, and provenance-report logic still do **not** exist (M5). The
`ArtifactKind` enum is in `app/models`.

### M4 boundaries (manifest read-model)

- **Picks are tri-state; PHI is dispositioned explicitly.** `include_pages` collate, `excluded_pages`
  are dropped, a page in neither is "not yet decided". `phi_disposition` defaults `pending` and moves
  only through `set_phi_disposition` (attorney-only); resolving the third-party-PHI **flag** does NOT
  auto-clear the exhibit (defense in depth — ADR-0006 decision 5).
- **Integrity is per-entry; blocking is matter-level (the M5-build-gate preview).** An entry is `ok`
  unless its include list is empty, a page is out of `1..page_count`, or its document was
  dedup-superseded (order: superseded > empty > out-of-range). `blocking` collects every non-`ok`
  verdict **plus** a `pending` PHI on any entry that HAS includes (an entry with nothing to collate
  isn't PHI-blocked). This is a *preview* — nothing here builds a PDF; the M5 build gate reads it.
- **EX minting is registry-only; the wire carries the BARE id, never a token.** `mint_tokens=True`
  mints `[[EX_n]]` for the `ok` entries in manifest order (1-based ordinal in the display form) in the
  ONE shared per-matter ordinal namespace — so an EX ordinal is **not** `EX_1` when facts/amounts
  minted first (it interleaves; inv 5). The route (`app/api/routes/evidence.py`) exposes the token as
  a bare id (`exhibit_token_id: "EX_1"`), never the token-shaped `[[EX_1]]` string (inv 11), and the
  entry also carries the Exhibit row id as `exhibit_id` so the workbench can drive the PHI endpoint
  (which is keyed by exhibit id) straight from the manifest view.

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
**redaction gate** (`pending` blocks the build) · **settled-tokens-only build** (BUS-05/
ADR-0012: `build_draft_manifest(require_settled_tokens=True)` stamps EX tokens READ-ONLY —
package assembly never mints or bumps the registry; a missing/drifted token raises
`ExhibitTokenUnsettled` → SSE `exhibit_tokens_unsettled`, and the completion fence re-locks
the matter before `artifacts_built` so an invalidation that won the race leaves the set as
immutable HISTORICAL output) · **authority gate** (BUS-02/ADR-0011:
`build_artifact_set`'s FIRST step — before the reuse fast-path and any mint/render/write —
verifies the matter's rule-pack pin via `load_pack_for_pin`, requiring an authoritative
pack whenever `app_env == "prod"` OR `require_audited_rule_pack_for_package`; typed
`RulePackUnaudited`/`RulePackUnpinned`/`RulePackChanged` refusals leave gate, storage,
rows, and audit state untouched, and an already-built set is never re-presented once its
pack fails authority).

## Change rule

A boundary change requiring a contract update: adding/removing an artifact kind or
changing the `ArtifactSet` keying / storage-key scheme / immutable-reuse rule (incl. the
ADR-0011 authority gate that now precedes reuse);
changing the no-token-survives build scan (`ArtifactTokenLeak`), the deterministic-bytes
contract (the pinned-metadata + `invariant=1` + fixed file-`/ID` recipe), or the
continuous-Bates scheme / `bates_prefix`; changing the binder build gate (`BinderBlocked`)
or the source-page integrity double-check (`BinderPageMissing`); changing the
letter's rendered-previews-only + memo-excluded rule; changing the provenance-report
completeness property, its three parts, or its re-runnable-module boundary; changing the
redaction-disposition gate; changing the artifact download route (the kind-keyed url /
object_key-never-on-wire / audit) or the binder-manifest shape the G3 check consumes;
changing the M4 manifest read-model — the `ManifestEntry` / `DraftBinderManifest` shape, the
pick-validation rules, the integrity-verdict order, the `blocking` preview semantics, the
EX-minting key/ordinal rule, or the bare-`exhibit_token_id` (never token-shaped) +
`exhibit_id` wire serialization. A change to any of these lands with a new ADR (cf.
[ADR-0007](../adr/0007-m5-drafting-decisions.md)). Update this file **and**
[`system_contract.md`](../system_contract.md) §2/10/11 in the same PR.
