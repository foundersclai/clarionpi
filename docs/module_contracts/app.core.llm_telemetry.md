# app.core.llm_telemetry

Backs [`system_contract.md`](../system_contract.md) invariant **12 — Per-Matter AI
Cost Is Metered And Capped** (the **ledger / single-door** half; the cap gate is
[app.core.matter_budget](app.core.matter_budget.md)).
Module path: `backend/app/core`.
Design source: [`backlog/pi/components/platform_core.md`](../../backlog/pi/components/platform_core.md).

## Status

**Implemented @ M0 (partial).** `app/core` exists with `config.py`, `db.py`, and
(in the parallel platform-core wave) `llm_provider.py`, `tenancy.py`, `audit.py`.
The `LlmCall` DTO is modeled in `app/models/schemas.py` and the default cap is in
`config.py`. The metered-client body, the `LLM_CALL` ledger writes, and the
meter-completeness CI guard land **M0–M1** (in progress).

## Responsibility

Owns the **metered LLM client (the single door)** and the **per-matter cost
accumulator** the budget gate reads. Every LLM provider call goes through the
metered `llm_provider` client; it writes an `LLM_CALL` ledger row
(`{matter, stage, model, tokens, cost}`) on **every** completion **before**
returning. There is **no unmetered provider handle anywhere** — the
`MeteredLLMClient` is the only path; a planted un-metered call fails the
meter-completeness test. Owns the stage enum used for attribution.

**Not responsible for:** the cap *policy* / HTTP 402 gate
([app.core.matter_budget](app.core.matter_budget.md)); provider *selection* or
model tiering (a config/`llm_provider` concern); pricing tables; business logic.

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | metered `llm_provider` client, per-matter cost accumulator, the stage enum | — |
| Consumes | every LLM call (the metered client is the only path) | all LLM-using components |
| Produces | `LLM_CALL` ledger rows + the per-matter cumulative cost | app.core.matter_budget (reads it) |
| Produces | `budget_warning`-feeding spend total (80% latch) | app.api.view_models (SSE) · app.engine.orchestrator |

## Invariants enforced

- **[12]** Per-matter AI cost is metered from day 1, ON by default, via the single
  metered client — **no undercounting, no side doors**. Cost attributes to a
  matter only inside the request-scoped session scope; each provider response is
  recorded **exactly once**.

## Vocabulary

`MeteredLLMClient` (the single door) · `LlmCall` (`matter_id`, `stage`, `model`,
`input_tokens`, `output_tokens`, `cost_cents`) · **stage enum**
(`classify · ocr_post · extract_encounter · extract_billing · chronology_narrative
· risk_flags · strategy_memo · draft_section · judge · assistant`) · the
per-matter cost accumulator (read by the budget gate).

## Cross-cutting (`app/core`)

`app/core` is also the home of **tenancy, append-only audit, and auth** — the
substrate every component stands on. Every firm-scoped table carries `firm_id`;
a `scoped_session(firm_id)` helper injects the tenancy predicate so no handler
issues an unscoped query (invariant **7** — PHI/BAA envelope, and the tenancy
basis for invariant **8**). Gate/PHI-access/artifact/export actions write an
append-only `AuditEvent` transactionally with the action — an audit-write failure
**fails the action** (invariant **9**). The BAA egress inventory and per-matter
run-log sink (invariant **14**) also live here. These surfaces are shared with
[app.core.matter_budget](app.core.matter_budget.md).

## Change rule

A boundary change requiring a contract update: adding a provider-call site that
does not route through the metered client; changing the `LlmCall` shape or the
stage enum; changing what counts as "recorded once"; changing the accumulator
contract the budget gate reads. Update this file **and**
[`system_contract.md`](../system_contract.md) §12 (and §7/9/14 for the
cross-cutting substrate) in the same PR.
