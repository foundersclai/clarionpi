# ADR-0016 — Draft and compliance authority

Status: accepted · Date: 2026-07-18 · Source: Workshop MVP plan set (WMVP-00/S1)

Decision owner: Draft and compliance authority.

Dependency: accept ADR-0016 before draft/compliance work.

## Context

Compliance findings evolve as deterministic checks, semantic review, corrections, overrides, and
re-review occur. Mutating one finding row or approving against a merely current draft would erase
the history needed to prove what G3 evaluated.

## Decision

Demand drafts and compliance findings are immutable histories. `DemandDraft` appends versions and
records its owning operation run. A finding has stable identity; each change appends a
`ComplianceFindingRevision` bound to the exact draft ID/version and, when produced by automated
work, the full source operation-run identity.

G3 approval binds the exact draft and exact compliance head it reviewed. Later draft/finding
revisions cannot inherit that approval. Gate-result bindings use the full ADR-0013 compound shapes.

ADR-0014 remains the owner of requested-demand election and exact approved-plan binding. This ADR
does not redefine how a draft selects its plan; it defines immutable draft/finding history and G3
authority after that binding exists.

## Consequences

- Every correction, override, and re-review remains auditable.
- G3 approval cannot float to a newer draft or compliance head.
- Operation-produced revisions retain their producer identity across retry/replay.
