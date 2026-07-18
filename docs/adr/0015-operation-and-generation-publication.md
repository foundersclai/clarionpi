# ADR-0015 — Operation ownership and generation publication

Status: accepted · Date: 2026-07-18 · Source: Workshop MVP plan set (WMVP-00/S1)

Decision owner: Operation ownership and generation publication.

Dependency: accept ADR-0015 before operation/generation work.

## Context

Long-running ingest, analysis, drafting, and publication work needs durable ownership. Result rows
also need to identify both their content generation and the operation that produced or reused it;
otherwise resumed work can publish a result from another firm, matter, or attempt.

## Decision

`MatterOperationRun(firm_id,matter_id,id)` is the durable unit of operational ownership. Resumed
runs, `LlmCall`, `PlanEmitAttempt`, and typed results reference the full run identity. A typed
preexisting/reuse result record is allowed only when it explicitly records the producing run.

Corpus, registry, evidence, and analysis publication is generation-addressed. Heads and processed
pointers reference a complete version/generation identity, and publication ordering makes the
result durable before its current pointer moves. Workshop `UploadSession`, operation,
`ProviderInvocation`, `GateRecord`, `ArtifactSet`, `CorpusVersion`, and `EvidenceVersion` rows bind
to the full `WorkshopEvidenceRun` scope.

ADR-0013 owns the candidate/reference inventory and intermediate-head rule. This ADR owns operation
and generation lifecycle, typed result ownership, retry identity, and publication order.

## Consequences

- Retry and resume use durable operation identity rather than request/process identity.
- Current pointers never advertise an unpublished generation.
- Reuse is explicit, typed, and attributable to its producing run.
