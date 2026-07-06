# app.rules.jurisdiction

Backs [`system_contract.md`](../system_contract.md) invariants **4, 13**.
Module path: `backend/app/rules`.
Design source: [`backlog/pi/components/jurisdiction_rules.md`](../../backlog/pi/components/jurisdiction_rules.md).

## Status

**Stub @ M0, lands M1–M2.** The package exists with a `packs/` data directory
(YAML is data, not code). The `DeadlineCandidate` model is in `app/models/schemas.py`.
The fail-loud loader, the AZ rule packs, and the `HybridEngine` lookup are not yet
implemented. **v1 = Arizona only.**

## Responsibility

The **lawyer-audited rules layer**: schema-validated YAML rule packs → typed,
diagnostic-carrying decisions. Port of the TM routing architecture
(**lawyer-audited YAML, engineer-owned Python**). Covers SOL + notice-of-claim
deadlines (claim type × party type, tolling, gov-entity notice traps),
comparative-fault regime, the billed-vs-paid flag for medicals, and time-limited-
demand statutory terms. Computes deadline **candidates + assumptions** for
attorney confirmation at G1.

**Not responsible for:** calendaring/reminder UI; arithmetic beyond date math
(none of the `Money` rollups — that is `app.money.ledger`); the LLM fallback for
unmatched situations (v1.x feature F2 — v1 emits an explicit `no_rule`
diagnostic); non-AZ packs (v1 is Arizona only).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | AZ YAML rule packs (`packs/`) + loader + `HybridEngine` | — |
| Consumes | matter facts: `claim_type`, `incident_date`, parties, party types | app.engine.orchestrator (via matter state) |
| Produces | `DeadlineCandidate[]` (surface at G1) | app.engine.orchestrator |
| Produces | billed-vs-paid basis flag per jurisdiction | app.money.ledger |
| Produces | statutory required terms (time-limited demand) | app.engine.brain2 |
| Produces | required-terms presence list | app.engine.compliance |

## Invariants enforced

- **[4]** Deadlines are deterministic and attorney-confirmed: candidates carry
  `assumptions` + `tolling_applied` and surface at G1; a matter **pins**
  `RulePackVersion` at confirm — a pack update prompts explicit re-confirm, never
  a silent reflow. The loader **refuses to start** on any `verified=false`,
  malformed, or audit-field-missing row (bad law must not run).
- **[13]** v1 is rules-only; the LLM fallback is a separately-typed v1.x path,
  never a code-side normalizer over YAML output. Consumers receive typed
  decisions + a `diagnostic.kind` the frontend trusts, never raw YAML.

## Vocabulary

`RuleRow` (`rule_id`, `statute_cite`, `verified_by`, `verified_date`,
`verified`) · `DeadlineCandidate` (`kind` ∈ {`sol`, `notice_of_claim`},
`computed_date`, `assumptions`, `tolling_applied`, `diagnostic`) · `Diagnostic.kind`
∈ {`matched`, `ambiguous`, `no_rule`} · `RulePackVersion` (`pack_id`, `version`,
`content_hash`) · billed-vs-paid **basis** (`billed` | `paid`).

## Change rule

A boundary change requiring a contract update: adding a rule kind or a
jurisdiction pack; changing the `DeadlineCandidate` / `Diagnostic` shape or the
`diagnostic.kind` set; changing the pack-schema required audit fields or the
fail-loud loader policy; changing the billed-vs-paid basis contract to
`app.money.ledger`. **Only `app/rules` reads jurisdiction YAML** — a consumer
reading YAML directly is a boundary breach. Update this file **and**
[`system_contract.md`](../system_contract.md) §4/13 in the same PR.
