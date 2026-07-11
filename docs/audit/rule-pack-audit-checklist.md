# Rule-Pack Legal Audit Checklist (counsel review)

The handoff for making a jurisdiction rule pack **authoritative** (BUS-02 —
[business-function-completeness](business-function-completeness.md)). A pack backs
production demand packages only after every item below is confirmed by counsel and the
pack's audit provenance is recorded. The technical gate is enforced in code:
`RulePack.is_authoritative` requires the audit flag + provenance AND every legal input
below to be `verify_status: verified`.

## Per deadline rule (`deadline_rules[*]`)

- [ ] **Statute citation** is correct and current (remove the `(verify — counsel)` marker).
- [ ] **Period** (`years`/`days`) matches the cited statute for the claim type.
- [ ] **Assumptions / tolling notes** are complete: minority, discovery rule, absence from
      state, public-entity notice interplay — anything an attorney must check at G1 is
      listed in `assumptions`, nothing silently assumed.
- [ ] `verify_status: verified`.

## Billed-vs-paid basis (`billed_vs_paid`)

- [ ] **Source** (e.g. the *Lopez* collateral-source line for AZ) supports the configured
      `basis` for this jurisdiction; the citation is verified primary authority.
- [ ] `verify_status: verified`.

## Demand-letter structure (`letter_structure`)

- [ ] **Section order and section set** reflect the firm's/jurisdiction's demand-letter
      judgment (this is legal drafting judgment, not formatting).
- [ ] **Required token kinds** per section are right (e.g. damages sections must carry
      `amount` tokens).
- [ ] Word ceilings are acceptable drafting guidance.
- [ ] `verify_status: verified`.

## Audit provenance (required the moment `audited: true`)

- [ ] `audited_by` — the reviewing counsel (name, bar qualifier as the firm prefers).
- [ ] `audited_at` — timezone-aware timestamp of the review.
- [ ] `audit_reference` — the durable reference (memo id, engagement letter, ticket).
- [ ] `audit_notes` — optional caveats/scope notes.

## Process

1. Counsel reviews against this checklist and records findings in the referenced memo.
2. A **legal-audit PR** updates the pack YAML only: flips `audited: true`, fills the
   provenance fields, flips each reviewed row to `verified`, and removes the
   `(verify — counsel)` markers. Code changes never ride a legal-audit PR.
3. The pack's `version` is bumped — existing matters stay pinned to the version and
   fingerprint they were created under (a later audit never retroactively authorizes
   earlier work; see ADR-0011).

## Production enforcement (context for counsel)

Production package builds REQUIRE an authoritative pack:
`REQUIRE_AUDITED_RULE_PACK_FOR_PACKAGE` defaults **on** when `APP_ENV=prod` and production
refuses to boot with it disabled — there is no production override. Dev/demo environments
may exercise the unaudited stub (the gate is off there by default); nothing built from an
unaudited pack can be a production deliverable.
