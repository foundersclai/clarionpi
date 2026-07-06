# ADR-0001: Adopt the PI Agent design suite as founding architecture

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** foundersclai

## Context

ClarionPI is a new MVP repo for an AI pipeline that turns personal-injury case files
(medical records, bills, police reports) into attorney-approved demand packages, built
for captive-firm deployment. A design suite for this product already exists at
`/Users/minimac/projects/TMEPAgent/backlog/pi` (vision/scope, high-level design, feature
list, tech stack, data model and contracts, implementation plan, competitive landscape,
captive-firm model, seed plan and budget, bootstrap ABS path, implementation readiness,
spike briefs, ABS ops runbook, plus component and system-flow diagrams), as of
2026-07-04. Rather than re-derive architecture from scratch during bootstrap, we need a
single founding reference the codebase can be checked against.

## Decision

We will adopt the PI Agent design suite at
`/Users/minimac/projects/TMEPAgent/backlog/pi` (as of 2026-07-04) as the founding
architecture for ClarionPI. The suite's binding invariants — the subset that must never
drift regardless of implementation detail — will be distilled into
`docs/system_contract.md` in this repo. Any deviation from the suite's design (module
boundaries, data model, gate sequencing, tech stack choices, etc.) requires a new ADR
recording what changed and why, rather than a silent divergence.

## Consequences

- Module boundaries (`api`, `core`, `models`, `engine`, `rules`, `money`, `corpus`,
  `package`) and the gate-machine shape can be scaffolded immediately without waiting
  on a fresh architecture pass.
- `docs/system_contract.md` becomes load-bearing: it is the first thing to read before
  any change that touches a module boundary, and it must stay in sync with the design
  suite's binding invariants.
- The design suite lives in a different repo (TMEPAgent), so ClarionPI's architecture
  has an external dependency for historical context; contributors need access to that
  path (or its contents mirrored into this repo's docs) to understand *why* a boundary
  is where it is.
- Future architecture changes that deviate from the suite are expected and allowed —
  they are not violations — but each one must be recorded as its own ADR so the
  drift is traceable, not silent.

## Alternatives Considered

- **Design ClarionPI's architecture from scratch, ignoring the PI suite** — rejected
  because the suite already encodes hard-won decisions (gate sequencing, captive-firm
  deployment model, tech stack) from a closely related product; re-deriving them would
  duplicate work without new information.
- **Copy the design suite's docs verbatim into this repo instead of referencing them** —
  rejected for M0: the suite is still evolving in TMEPAgent, and forking it immediately
  would require manually tracking two copies. Revisit if/when the suite stabilizes or
  ClarionPI's needs diverge enough to warrant its own copy.
