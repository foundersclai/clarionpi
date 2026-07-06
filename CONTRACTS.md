# ClarionPI — Contracts Drift Matrix

This table maps each module path to its contract doc under `docs/module_contracts/`
(module boundary, inputs/outputs, invariants). `make hub-check` parses this table and
fails the build if a listed module path or contract doc goes missing — the matrix and
the filesystem must never drift apart. Do not remove a row when deleting a module;
delete the module's contract doc too, in the same commit.

Per-module boundaries refine the top-level [`docs/system_contract.md`](docs/system_contract.md)
(the 14 invariants); see [`docs/module_contracts/README.md`](docs/module_contracts/README.md)
for how the three layers relate and the contract-first-change workflow.

At **M0** most modules are package stubs — the `notes` column states each module's
status truthfully (`implemented`/`stub`). `corpus` and `core` each back two contracts
(shared directory, distinct boundaries).

| module path | contract doc | notes |
|---|---|---|
| backend/app/corpus | docs/module_contracts/app.corpus.ingest.md | status: live (M1) — raw uploads to page store (sessions, classify, OCR fallback, dedup, phase0 SSE) |
| backend/app/corpus | docs/module_contracts/app.corpus.extraction.md | status: stub (lands M2) — pages to anchored typed facts |
| backend/app/engine/orchestrator | docs/module_contracts/app.engine.orchestrator.md | status: implemented (partial) — gate machine + audit |
| backend/app/engine/brain2 | docs/module_contracts/app.engine.brain2.md | status: stub (lands M5) — approved structure to tokenized prose |
| backend/app/engine/compliance | docs/module_contracts/app.engine.compliance.md | status: stub (lands M5) — G3 deterministic + semantic panel |
| backend/app/engine/tokenizer | docs/module_contracts/app.engine.tokenizer.md | status: stub (lands M2) — the fact registry / token spine |
| backend/app/rules | docs/module_contracts/app.rules.jurisdiction.md | status: stub (lands M1-M2) — lawyer-audited YAML to decisions |
| backend/app/money | docs/module_contracts/app.money.ledger.md | status: stub (lands M2) — all Money arithmetic (integer cents) |
| backend/app/package | docs/module_contracts/app.package.builder.md | status: stub (lands M5) — docx letter + Bates binder |
| backend/app/api | docs/module_contracts/app.api.view_models.md | status: implemented (partial) — the only wire surface |
| backend/app/core | docs/module_contracts/app.core.llm_telemetry.md | status: implemented (partial) — metered single door + ledger |
| backend/app/core | docs/module_contracts/app.core.matter_budget.md | status: implemented (partial) — per-matter cap gate + warnings |
