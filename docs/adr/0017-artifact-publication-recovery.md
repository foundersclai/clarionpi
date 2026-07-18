# ADR-0017 — Artifact publication and recovery

Status: accepted · Date: 2026-07-18 · Source: Workshop MVP plan set (WMVP-00/S1)

Decision owner: Artifact publication and recovery.

Dependency: accept ADR-0017 before publication work.

## Context

Package assembly produces several immutable bytesets and may be retried after process failure.
Publishing directly to final keys makes collisions, partial visibility, and recovery ownership
ambiguous—especially when a prior operation already produced reusable artifacts.

## Decision

Artifact publication follows reserve, stage, then publish. A durable `ArtifactPublication` owns
the collision-free reservation and state; staged bytes are not externally visible. Publication
moves only after the complete `ArtifactSet` is durable and bound to the exact draft, G3
`GateRecord`, operation run, and publication authority.

Recovery belongs to the publication/run identity. A retry resumes or conclusively abandons the
same reservation; it does not invent a second final key. Reuse requires an explicit
`ArtifactReuseRecord(firm_id,matter_id,id,operation_run_id,artifact_set_id)` and never mutates the
original set.

ADR-0013 owns the full candidate/reference inventory. ADR-0015 owns general operation/result
publication ordering. This ADR owns artifact reservation, visibility, collision handling, reuse,
and recovery.

## Consequences

- Consumers never observe a partially published package.
- Final keys remain collision-free across retry, recovery, and reuse.
- Immutable artifact history remains attributable to the draft, G3 approval, and producing run.
