# ClarionPI — Contracts Drift Matrix

This table maps each module path to its contract doc under `docs/module_contracts/`
(module boundary, inputs/outputs, invariants). `make hub-check` parses this table and
fails the build if a listed module path or contract doc goes missing — the matrix and
the filesystem must never drift apart. Do not remove a row when deleting a module;
delete the module's contract doc too, in the same commit.

| module path | contract doc | notes |
|---|---|---|
<!-- TODO(docs-wave): populate this table when the module contract docs land (see
     backend/app/{api,core,models,engine,rules,money,corpus,package}). Until then this
     table is intentionally empty and `make hub-check` reports 0 modules. -->
