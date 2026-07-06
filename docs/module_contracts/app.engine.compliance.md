# app.engine.compliance

Backs [`system_contract.md`](../system_contract.md) invariants **2, 3, 6, 11, 13**.
Module path: `backend/app/engine/compliance`.
Design source: [`backlog/pi/components/compliance_engine.md`](../../backlog/pi/components/compliance_engine.md).

## Status

**Stub @ M0, lands M5.** The package exists (empty `__init__.py`, created as
scaffolding). The `FindingBucket`/`FindingGating` enums are in `app/models`. No
deterministic checks, semantic judge, or finding-lifecycle logic exists yet.

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
regenerated) → re_verified → dispositioned`) · **bucket routing** (mechanical →
span-patch for an *enumerated* set; **conservative default = semantic → regen**) ·
**re-verify always runs after a patch/regen** (a fix that introduces a new orphan
is caught) · hard blocks (never overridable to ship): `orphan_token`,
`amt_ledger_mismatch`, `dead_anchor`, `missing_exhibit`, `undisposed_adverse`,
`registry_version` mismatch.

## Change rule

A boundary change requiring a contract update: adding/removing a `check_kind` or
changing its `bucket`/`severity`; changing the hard-block set; changing the
mandatory re-verify-after-fix rule or the snapshot-symmetry contract with
`app.engine.brain2`; changing the AMT re-verification contract with
`app.money.ledger`. Update this file **and**
[`system_contract.md`](../system_contract.md) §2/3/6/11/13 in the same PR.
