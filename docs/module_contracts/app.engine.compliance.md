# app.engine.compliance

Backs [`system_contract.md`](../system_contract.md) invariants **2, 3, 6, 11, 13**.
Module path: `backend/app/engine/compliance`.
Design source: [`backlog/pi/components/compliance_engine.md`](../../backlog/pi/components/compliance_engine.md).

## Status

**Live @ M5.** The panel is implemented + tested: `checks.py` (the seven deterministic
predicates), `judge.py` (the Sonnet judge + snapshot symmetry + the fail-visible TONE
marker), `corrections.py` (span-patch, single-section regen, and the mandatory
re-verify), and `engine.py` (the pass, severity/bucket routing, the finding lifecycle,
the attorney disposition, and `open_blocking_count`). Wired on the wire by
`app/api/routes/drafting.py` (the finding-action route + the `post_draft` G3 pre-check
hook); its `open_blocking_count` feeds the G3 `no_blocking_findings` guard through
`app.engine.orchestrator.service.build_guard_context`. Decisions recorded in
[ADR-0007](../adr/0007-m5-drafting-decisions.md).

## Responsibility

The **G3 panel**. Runs two check families over a rendered `DEMAND_DRAFT` and emits
typed `ComplianceFinding`s the attorney dispositions before the draft can become a
package. **Deterministic checks (pure code):** every token resolves at the pinned
`registry_version`; every `[[AMT_n]]` matches the live ledger (`ledger_hash`
re-verified, not trusted); anchors are live; `[[EX_n]]` refs exist in the binder
manifest; statutory required terms are present when active; risk dispositions are
respected. **Semantic checks (Sonnet judge):** the judge sees the drafter's
**exact prompt snapshot** (symmetry) and flags unsupported causation, strategy
drift, tone. G3 approve requires **zero open blocking findings**.

**Not responsible for:** drafting/regenerating prose (`app.engine.brain2` does the
regen; this component only *commands* it); rendering/detokenization
(`app.package.builder` + `app.api.view_models`); risk-flag **severity policy**
(brain1 risk owns that — this component only checks the *disposition* was honored).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | `ComplianceFinding` + the finding lifecycle | — |
| Consumes | `DraftSection[]` (rendered) + the drafter prompt snapshot | app.engine.brain2 |
| Consumes | token → resolution (anchors, verified status) | app.engine.tokenizer |
| Consumes | ledger totals + `ledger_hash` (AMT re-verify) | app.money.ledger |
| Consumes | binder manifest (EX-ref existence) | app.package.builder |
| Consumes | time-limited required-terms list | app.rules.jurisdiction |
| Consumes | risk dispositions (address/omit/need-more) | brain1 (risk) |
| Produces | G3 payload (findings, buckets) | app.api.view_models |
| Produces | span-patch commands / section-regen commands | app.package.builder · app.engine.brain2 |

## Invariants enforced

- **[2]** Orphan tokens / dead anchors are **hard G3 blocks**; nothing unanchored
  ships.
- **[3]** Every `[[AMT_n]]` is re-verified against the ledger `ledger_hash` at G3
  (catches a ledger edit landing after render).
- **[6]** No adverse fact in prose without disposition = `address_in_letter`;
  `undisposed_adverse` is a hard block.
- **[11]** Findings carry anchors (what the attorney sees, not a paraphrase); the
  panel drives the sentinel/block behavior, never a fabricated fix.
- **[13]** Semantic = the Sonnet judge; deterministic = code predicates; **no
  regex/allowlist patching of legal semantics** on either side. A judge/drafter
  snapshot-hash mismatch **fails the run loudly** — the judge must grade the
  drafted world, not a drifted one.

## Vocabulary

`ComplianceFinding` (`check_kind`, `severity` ∈ {`blocking`, `advisory`},
`bucket` ∈ {`mechanical`, `semantic`}, `span?`, lifecycle `open → (patched |
regenerated) → re_verified → dispositioned`).

**Deterministic checks** (`checks.py`, code predicates, one per `CheckKind`, run in
order): `orphan_token`, `amt_ledger_mismatch` (each `[[AMT_n]]` re-verified against the
LIVE ledger hash — a `None` live hash flags every AMT, fail-visible), `dead_anchor` (the
page-bounds probe the registry mint-time integrity check does NOT do — an anchor page
beyond `page_count` or on a dedup-superseded document; the offending anchors ride the
finding), `missing_exhibit`, `missing_statutory_term` (v1 no-op seam), `undisposed_adverse`
(ONE finding when any adverse flag is undispositioned), `prose_total_mismatch` (a literal
`$…` in a rendered preview matching no AMT display form). Findings are created OPEN +
uncommitted; the engine owns persistence/severity/bucket.

**Bucket routing** (`bucket_for`): `MECHANICAL_KINDS` (`amt_ledger_mismatch`,
`missing_exhibit`, `missing_statutory_term`, `prose_total_mismatch`) → span-patch; every
other kind → `semantic` (regen) by the **conservative default** (an unknown/ambiguous
kind is semantic; total over `CheckKind`).

**Severity** (`_severity_for`): **all BLOCKING at v1** — `ADVISORY` is unused; the
override effect rides `status`, not severity (an override drops a finding out of
`open_blocking_count` via the DISPOSITIONED status — ADR-0007). `open_blocking_count`
counts `blocking` findings whose `status` is NOT in {`re_verified`, `dispositioned`}, over
the matter's latest draft.

**Hard blocks** (`HARD_BLOCK_KINDS`, never overridable to ship — `HardBlockNotDisposable`):
`orphan_token`, `amt_ledger_mismatch`, `dead_anchor`, `missing_exhibit`,
`undisposed_adverse`. A `registry_version` mismatch is a hard block too, but it is the pass
PRECONDITION (`DraftRegistryDrift`) + the G3 guard, not a finding. `prose_total_mismatch`
is BLOCKING but NOT hard (mechanically fixable/overridable, unlike an orphan). Any hard
block SHORT-CIRCUITS the pass (the judge does not run — cheap-first).

**Judge symmetry** (`judge.py`): before grading, the judge rebuilds the drafter's
`DrafterPromptSnapshot` from freshly-built constraints + the planned contract and compares
`input_hash` to the persisted one — a mismatch raises `SnapshotDrift` (never grades a
drifted world; checked for ALL sections before any spend). It is then handed the PERSISTED
snapshot blocks verbatim + the rendered preview, and flags ONLY the three semantic kinds
(`unsupported_causation` / `strategy_drift` / `tone`; the `JudgeFindingBatch` schema rejects
a mechanical kind). A judge double-failure (no valid verdict after one retry) emits ONE
BLOCKING **fail-visible TONE marker** ("manual review required") rather than passing the
section clean — `tone` is reused so the marker rides the semantic bucket without a new
`check_kind`. A provider/budget outage is a typed `JudgeUnavailable` → an honest
`judge_skipped` (the deterministic findings still stand).

**Corrections** (`corrections.py`): `apply_span_patch` = a deterministic re-render, with a
RUNTIME ESCALATION to regen (drop the finding into the semantic bucket) when the re-rendered
section fails validation — the TM safety net, never ship an invalid splice. `request_section_regen`
re-drafts the section IN PLACE, passing the finding's detail through the drafter's
`retry_violations` channel (snapshot-neutral, so the regenerated snapshot reproduces from
`build_hard_constraints` and the re-verify judge never spuriously raises `SnapshotDrift` — the
`SnapshotDrift` root-cause decision), WITHOUT the machine's `SEMANTIC_FINDING_REGEN` round-trip
(state-agnostic; the edge is reserved for the FE long-form flow). **Re-verify ALWAYS runs after a
patch/regen** (a fix that introduces a new orphan is caught): a resolved finding flips to
`re_verified`, a still-failing one stays, a NEW one is created `open`.

**Guard feed** (`engine.py`): `open_blocking_count` over the latest draft feeds
`build_guard_context`'s `blocking_findings` → the G3 `no_blocking_findings` guard.

## Change rule

A boundary change requiring a contract update: adding/removing a `check_kind` or
changing its `bucket`/`severity` (incl. the all-BLOCKING-v1 rule + the `MECHANICAL_KINDS`
set); changing the hard-block set or the cheap-first short-circuit; changing the mandatory
re-verify-after-fix rule, the span-patch-with-runtime-escalation contract, the
snapshot-neutral regen channel, or the snapshot-symmetry / fail-visible-TONE contract with
`app.engine.brain2`; changing the AMT re-verification (live-ledger) contract with
`app.money.ledger`; changing the `open_blocking_count` semantics the G3 guard reads. A
change to any of these lands with a new ADR (cf.
[ADR-0007](../adr/0007-m5-drafting-decisions.md)). Update this file **and**
[`system_contract.md`](../system_contract.md) §2/3/6/11/13 in the same PR.
