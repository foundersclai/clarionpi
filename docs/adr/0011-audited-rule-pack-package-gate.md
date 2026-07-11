# ADR-0011 — Audited rule-pack gate: matter pins + build-time authority check

Status: accepted · Date: 2026-07-11 · Source: business-completeness audit
(`docs/audit/plans/03-audited-az-rule-pack-gate.md`, BUS-02) · cf. ADR-0007 (the
immutable-ArtifactSet build contract this composes with)

## Context

The AZ rule pack is a deliberately unaudited stub, yet nothing between it and a finished
demand package checked `audited` at all — the package SSE path called `build_artifact_set`
without consulting the pack. Making the boolean meaningful requires deciding *what*
authority means, *where* it is enforced, and *which* pack a matter's work attests to.

## Decision 1 — authority is provenance + full verification, never a boolean

`audited: true` is refused at validation without counsel provenance (`audited_by`,
timezone-aware `audited_at`, `audit_reference`), and `RulePack.is_authoritative`
additionally requires a non-empty fully-`verified` deadline set, a present `verified`
`billed_vs_paid`, and a present `verified` `letter_structure` — every legal input
drafting/package assembly consumes. The counsel handoff is
`docs/audit/rule-pack-audit-checklist.md`.

## Decision 2 — matters pin the exact pack; the pin is the authority reference

`Matter.rule_pack_version` + `rule_pack_fingerprint` (SHA-256 over the complete canonical
pack model) are written at creation. Every post-create rules consumer that can feed the
package — Phase-0/analysis ledger sync (preflighted at ENTRY, pack reused, never re-loaded
mid-run), evidence edit recomputation, Brain-2 plan emission, compliance ledger hashing,
and the package build — goes through `load_pack_for_pin`, which refuses version/fingerprint
drift **before that workflow's first write**. Consequences:

- A pack edited after creation (even if reverted before package build) cannot be consumed
  against the pin — change-then-revert is caught at the stage that would have consumed it.
- A pack audited AFTER a matter was created cannot retroactively authorize that matter's
  work: the fingerprint no longer matches (`rule_pack_changed`).
- Legacy pre-pin matters are deliberately NOT backfilled (that would falsely attest their
  work used today's pack); the enabled guard fails closed on missing pins
  (`rule_pack_unpinned`), and dev/test (guard off) keeps serving them.

## Decision 3 — the guard lives in the `app.package` domain, before reuse

`build_artifact_set` enforces authority as its FIRST step — before the immutable-set reuse
fast-path, manifest/EX-token minting, artifact rendering, storage writes, rows, or audit
events — so an already-built set is never re-presented once its pack fails authority, and
direct/background callers cannot bypass a wire-layer check. Enforcement keys on
`app_env == "prod" OR require_audited_rule_pack_for_package`: a production process that
never ran the FastAPI lifespan still cannot disable the gate (startup validation refusing
`false` in prod is defense in depth, not the mechanism). This tightens ADR-0007's reuse
rule: reuse now happens only for a pack that passes the (enabled) authority check.

## Wire contract

Typed refusals only, nothing sensitive: the package SSE maps `rule_pack_unaudited`
(+ jurisdiction + pack_version), `rule_pack_unpinned`, `rule_pack_changed`,
`jurisdiction_unsupported`, and `rule_pack_invalid` — no fingerprints, exception strings,
file paths, audit notes, or legal citations in frames. REST consumers refuse `409
{error: <diagnostic_kind>}`; the read-only evidence view renders a `None` ledger under
drift rather than recomputing against unattested law.
