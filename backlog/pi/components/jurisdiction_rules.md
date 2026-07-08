# Component — jurisdiction_rules

- **Status:** DRAFT for founder review · **Date:** 2026-07-04
- **Planned module path:** `app/rules`
- **Contract doc (M0):** `docs/module_contracts/app.rules.jurisdiction.md`
- Refines [04 §2 `DeadlineCandidate`/`StrategyPlan`](../04_data_model_and_contracts.md) and
  [01 §6 rules layer](../01_high_level_design.md). **v1 = Arizona ONLY** per [../07 §5](../07_captive_firm_model.md).

## 1. Responsibility

The **lawyer-audited rules layer**: YAML rule packs → typed, diagnostic-carrying decisions.
Port of the TM routing architecture (**lawyer-audited YAML, engineer-owned Python**). Packs
cover SOL + notice-of-claim deadlines (claim type × party type, tolling flags, gov-entity
notice traps), comparative-fault regime, billed-vs-paid flag for medicals, time-limited-demand
statutory requirements (v1.x, feature D7), and solicitation constraints (informational, for
intake ops). Computes deadline **candidates + assumptions** for attorney confirmation at G1.

**NOT responsible for:** calendaring/reminder UI; marketing execution; non-AZ packs (v1 is
Arizona only); the LLM fallback for unmatched situations (lands v1.x, feature F2 — v1 emits
an explicit "no rule" diagnostic instead).

## 2. Boundary

| Direction | What | Peer component |
|---|---|---|
| consumes | matter facts: claim_type, incident_date, parties, party types | orchestrator_gates.md (via matter state) |
| owns | AZ YAML rule packs + loader + `HybridEngine` | — |
| produces | `DeadlineCandidate[]` (surface at G1) | orchestrator_gates.md |
| produces | billed-vs-paid flag per jurisdiction | money_engine.md |
| produces | statutory required terms (time-limited demand) | brain2_drafting.md |
| produces | required-terms presence checks | compliance_engine.md |

## 3. Key types & fields

```python
class RuleRow:                             # every YAML row, schema-validated at boot
    rule_id: str; statute_cite: str        # e.g. "A.R.S. § 12-542"
    verified_by: str; verified_date: date  # legal cofounder audit ([../05](../05_implementation_plan.md))
    verified: bool                         # ships ONLY if true (loader rejects false)
    claim_type: str; party_type: str; jurisdiction: str   # v1: "AZ"
    payload: JsonValue                     # rule-kind specific (period, tolling, regime…)

class DeadlineCandidate:                   # extends 04 §2 — never a single silent date
    rule_id: str; deadline_kind: Literal["sol","notice_of_claim"]
    computed_date: date; assumptions: list[str]      # what the computation assumed
    tolling_applied: list[str]             # e.g. ["minority"]
    diagnostic: Diagnostic                 # kind the FE can trust (matched | ambiguous | no_rule)

class RulePackVersion:                     # matter pins this at G1 confirm
    pack_id: str; version: str; content_hash: str
```

## 4. Internal design

- **Loader + schema validation at boot (fail loud):** a malformed pack or any `verified=false`
  / missing `{rule_id, statute_cite, verified_by, verified_date}` row causes **refuse-to-start**
  — never a silent skip. Bad law must not run (TM fabricated-foundational-cites lesson;
  [01 §6](../01_high_level_design.md) "verify every statutory detail before the YAML ships").
- **Pure deadline computation → candidates, not a date:** returns `DeadlineCandidate[]` with
  explicit `assumptions` and `tolling_applied`; the attorney confirms at G1 (invariant 4).
  Date math is pure code (invariant 3) — the rules layer never computes silently or emits a
  lone unconfirmed date.
- **Typed diagnostics the FE trusts (lawyer-audit boundary pattern):** every decision carries
  `diagnostic.kind` ∈ `{matched, ambiguous, no_rule}`. Ambiguous claim-type mapping returns
  **multiple** candidate sets + `ambiguous`; unmatched returns `no_rule` (no guessing — the
  LLM fallback is v1.x F2). Frontend renders on `kind`, never re-derives.
- **Rule-pack versioning (no silent reflow):** a matter **pins** `RulePackVersion` at G1
  confirm. A later pack update prompts explicit **re-confirm**; it never silently reflows an
  already-confirmed deadline (parity with orchestrator registry-version pinning).
- **Boundary discipline:** consumers receive typed decisions + diagnostics — never raw YAML
  ([04 §5](../04_data_model_and_contracts.md): "only `rules/` reads jurisdiction YAML").

## 5. Invariants enforced

- **4** — deadlines are deterministic and attorney-confirmed: candidates surface at G1;
  non-dismissible until confirmed; pack version pinned at confirm.
- **13** — semantic vs deterministic split honored: v1 is rules-only; the LLM fallback is a
  separately-typed v1.x path, not a code-side normalizer over YAML output.
- **Lawyer-audited → YAML / engineer → Python** boundary (TM routing-policies-layer pattern).

## 6. Failure modes & handling

| Failure | Detection | Handling |
|---|---|---|
| Unverified / malformed row in pack | Boot schema validation | Loader rejects pack → refuse to start (fail loud) |
| Ambiguous claim-type → party mapping | >1 rule matches | Return multiple candidate sets + `diagnostic.kind = ambiguous` |
| No rule for the situation (v1) | Zero matches | Return `diagnostic.kind = no_rule`; attorney handles manually (F2 later) |
| Pack updated after a matter pinned it | `content_hash` differs from pin | Prompt re-confirm at the owning gate; never silent reflow |
| Missing tolling input (e.g. DOB for minority) | Required field absent | Assumption noted in `assumptions[]`; surfaced for attorney at G1 |

## 7. Test strategy

- **Golden deadline grids per claim type** (AZ): SOL + notice-of-claim across party types,
  including tolling (minority) and gov-entity notice traps; expected `computed_date` +
  `assumptions` asserted.
- **Pack-version pinning:** pin at G1, bump pack → matter still reads the pinned version;
  re-confirm prompt fires; no silent recompute.
- **Loader rejection:** `verified=false`, missing audit fields, and malformed payloads each
  cause refuse-to-start; a fully-verified pack boots clean.
- **Diagnostic kinds:** ambiguous mapping yields multiple sets; unmatched yields `no_rule`.

## 8. Open questions

1. AZ gov-entity notice-of-claim (A.R.S. § 12-821.01, 180-day) edge cases — which party-type
   permutations ship in the v1 pack? (Legal cofounder audit scope; verify each cite.)
2. Billed-vs-paid: does AZ's collateral-source posture need a per-provider flag, or one
   matter-level flag to `money_engine`? (Affects the ledger's display, not its arithmetic.)
3. Solicitation-constraint rows are informational (intake ops) — do they belong in this pack
   or a sibling `intake` pack? (Keeps legal-deadline packs single-purpose; leaning sibling.)
