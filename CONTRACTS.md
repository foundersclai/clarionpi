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
| backend/app/corpus | docs/module_contracts/app.corpus.extraction.md | status: live (M2) — pages to anchored typed facts (windows, extractors, anchor-validation, merge; reconciliation llm_only until S1) |
| backend/app/engine/orchestrator | docs/module_contracts/app.engine.orchestrator.md | status: implemented (M3) — gate machine (M0) + gate-action service `apply_gate_action` + audit mirror (M3); run coordination/G2.5/G3 side effects land M4-M5 |
| backend/app/engine/brain2 | docs/module_contracts/app.engine.brain2.md | status: stub (lands M5) — approved structure to tokenized prose |
| backend/app/engine/compliance | docs/module_contracts/app.engine.compliance.md | status: stub (lands M5) — G3 deterministic + semantic panel |
| backend/app/engine/tokenizer | docs/module_contracts/app.engine.tokenizer.md | status: live (M2) — the fact registry / token spine (mint + versioning + prompt/render resolution) |
| backend/app/engine/brain1 | docs/module_contracts/app.engine.brain1.chronology.md | status: live (M2) — derived chronology rows + overlays + tokens-only narratives |
| backend/app/engine/brain1 | docs/module_contracts/app.engine.brain1.risk.md | status: live (M4) — risk detectors + disposition workflow |
| backend/app/rules | docs/module_contracts/app.rules.jurisdiction.md | status: live partial (M1-M2) — lawyer-audited YAML to decisions (loader + AZ pack + billed-vs-paid basis; HybridEngine lookup later) |
| backend/app/money | docs/module_contracts/app.money.ledger.md | status: extended (M2) — all Money arithmetic (integer cents); specials ledger + dedup exclusion + [[AMT]] emission |
| backend/app/package | docs/module_contracts/app.package.builder.md | status: partial (M4) — manifest read-model (picks + PHI disposition + EX-mint + blocking preview); docx/pdf/xlsx builds + Bates land M5 |
| backend/app/api | docs/module_contracts/app.api.view_models.md | status: implemented (M3) — the only wire surface; auth/roles + gate envelope + per-gate VMs + wire token-scanner + closed submit schemas; SSE replay deferred M4/M5 |
| backend/app/core | docs/module_contracts/app.core.llm_telemetry.md | status: implemented (partial) — metered single door + ledger |
| backend/app/core | docs/module_contracts/app.core.matter_budget.md | status: implemented (partial) — per-matter cap gate + warnings |
