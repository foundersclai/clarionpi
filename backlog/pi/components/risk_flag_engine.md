# Component — risk_flag_engine

- **Status:** DRAFT for founder review · **Date:** 2026-07-04
- **Planned module path:** `app/engine/brain1/risk`
- **Contract doc (M0):** `docs/module_contracts/app.engine.brain1.risk.md`
- Refines [04 §2 `RiskFlag`](../04_data_model_and_contracts.md) and [01 §7 risk table](../01_high_level_design.md);
  does not contradict them.

## 1. Responsibility

Detect adverse / case-risk facts and **force a human disposition** before drafting. Run the
[01 §7](../01_high_level_design.md) detector suite, emit anchored `RiskFlag` rows, and drive
the G2a disposition workflow whose output becomes hard constraints for `brain2_drafting` and
checks for `compliance_engine`.

**NOT responsible for:** the letter's rhetoric or *how* a risk is addressed (attorney at
G1.5/G2.5 + Brain-2); redaction *execution* (`package_builder` — this engine only routes
`third_party_phi` to it); arithmetic (`money_engine`) or minting tokens (`fact_registry`).

## 2. Boundary

| Direction | What | Peer component |
|---|---|---|
| consumes | `MedicalEncounter[]`, `IncidentFacts`, intake answers | corpus_extraction.md |
| consumes | `[[FACT_n]]` anchors/display forms (for flag anchors) | fact_registry.md |
| owns | `RiskFlag` rows + dispositions | — |
| produces | disposition `address` list + no-volunteer set → constraints | brain2_drafting.md |
| produces | disposition checks (no dropped-high, no volunteered-adverse) | compliance_engine.md |
| produces | `third_party_phi` redaction targets | package_builder.md |
| gated by | G2a confirm (paralegal prep, attorney confirm) | orchestrator_gates.md |

## 3. Key types & fields

```python
class RiskFlag:                            # extends 04 §2
    id: UUID; matter_id: UUID
    kind: RiskKind                         # 01 §7 taxonomy (7 kinds)
    severity: Literal["low","medium","high"]
    anchors: list[PageAnchor]; detail: str
    detector: Literal["date_math","label","heuristic_llm"]  # provenance of the flag
    disposition: Literal["address_in_letter","omit_with_rationale","need_more_records"] | None
    disposition_by: UUID | None; disposition_role: str | None
    disposition_rationale: str | None      # required for omit_with_rationale

class GapDetectorConfig:                   # deterministic knobs (invariant 3-adjacent)
    max_gap_days_pre_mmi: int = 30         # configurable per firm/jurisdiction
    per_kind_cap: dict[RiskKind, int]      # flood guard — cap count, never auto-drop
```

## 4. Internal design

- **Determinizable → code (scope principle):** `treatment_gap` is pure date math over sorted
  encounter dates (gap > `max_gap_days_pre_mmi`, default 30, configurable); `low_property_damage`
  compares damage estimate vs injury-severity band; `preexisting_condition` / `prior_claim`
  come from extractor labels + intake answers; `liability_weakness` from police-report fault
  indicators. No LLM where a rule suffices.
- **Semantic → LLM (scope principle, invariant 13):** `degenerative_finding` is LLM-**labeled**
  from imaging-report language (never regex — TM no-regex-on-semantic lesson); `causation_ambiguity`
  is a mechanism-vs-injury heuristic backstopped by LLM. Labels carry the same anchors as any
  fact so the attorney reads the page, not a paraphrase.
- **Disposition workflow (G2a):** each flag needs `address_in_letter` / `omit_with_rationale`
  / `need_more_records`. **High-severity flags require an attorney disposition**, enforced
  server-side (invariant 8); paralegals may prep dispositions but cannot confirm G2a.
- **Constraint handoff:** `address_in_letter` flags become a hard-constraint *address list*
  for Brain-2; everything else joins the **no-volunteer** set. Brain-2 must address the first
  and must never surface the second (invariant 6). The same lists feed `compliance_engine`:
  no dropped high-severity flag, no volunteered adverse fact.
- **Flood control without silent loss:** per-kind caps (`per_kind_cap`) plus severity tuning
  bound noise; capped flags are still surfaced (attorney-triaged), **never auto-dropped** —
  suppression of an adverse fact is the one thing this engine must not do.

## 5. Invariants enforced

- **6** — adverse facts surfaced always, volunteered never: no flag reaches the letter without
  `disposition = address_in_letter`; the no-volunteer set is a Brain-2 + compliance constraint.
- **8** — role-gated: high-severity disposition is attorney-only, server-enforced; paralegal
  prep does not confirm G2a.
- **13** — semantic detectors (`degenerative_finding`, `causation_ambiguity`) are LLM checks,
  not code regex/allowlists.

## 6. Failure modes & handling

| Failure | Detection | Handling |
|---|---|---|
| Detector false-positive flood | Count exceeds `per_kind_cap` | Cap surfaced set; keep all as attorney-triaged; never auto-drop |
| `third_party_phi` missed here | — | Backstop scan in `package_builder` pre-binder (defense in depth) |
| High-severity flag left undispositioned | G2a confirm guard | Block G2a confirm until attorney dispositions it |
| `omit_with_rationale` with empty rationale | Field validation | Reject disposition; rationale mandatory for audit |
| LLM label unanchored | Missing `anchors` | Fail the flag build (invariant 2); retry labeling |

## 7. Test strategy

- **Gap detector unit grid:** date sequences × MMI dates × config thresholds → expected
  flags (boundary at exactly 30d; multi-gap; pre- vs post-MMI).
- **Disposition gating:** undispositioned high-severity flag blocks G2a confirm; attorney-only
  enforcement asserted at the role boundary.
- **Constraint handoff:** on fixtures, `address_in_letter` set appears in Brain-2 hard
  constraints and the no-volunteer set is honored (planted adverse fact stays out of draft).
- **Semantic detector labeling:** imaging-language fixtures produce `degenerative_finding`
  labels; regex-only phrasing variants still caught (LLM, not pattern).

## 8. Decisions & open questions

**Decided 2026-07-04** (recorded in [10_implementation_readiness.md](../10_implementation_readiness.md) §4):

- **`need_more_records` leaves the flag OPEN, not resolved.** G2a confirm with any open
  high-severity flag is `requires_override` — the attorney proceeds eyes-open with a
  mandatory reason, audited (attorney-final + override-audit principle). The open flag
  persists into Brain-2 hard constraints ("do not overstate") and appears in the G3 payload
  as informational; arriving records re-enter via
  [flow_04](../system_flows/flow_04_late_records_rework.md).
- **MMI date is an explicit attorney-set field at G1.5** — not inferred from the last
  encounter or an extractor label (trust-attorney-HITL for treatment-posture facts; the
  gap detector reads one authoritative field).

Open:

1. `low_property_damage` severity band thresholds — firm-configurable, or jurisdiction-tied
   via `jurisdiction_rules`? (Damage-vs-injury ratio needs attorney calibration data from
   the first live matters.)
