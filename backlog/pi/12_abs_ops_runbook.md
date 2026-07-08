# PI Agent — ABS Operations Runbook (Scaffold)

- **Status:** DRAFT SCAFFOLD — **for ethics counsel to finalize; nothing here is legal
  advice; counsel owns the final text.** · **Date:** 2026-07-04
- **Purpose:** the operating rules the captive AZ ABS firm runs under, per
  [07](./07_captive_firm_model.md). Every legal specific is marked **(verify — counsel)** and
  is a placeholder until counsel confirms it against current Arizona authority.
- **Audience:** managing attorney (runbook owner once hired), compliance lawyer, founders.
- This scaffold assumes the entity + gates in [07 §3](./07_captive_firm_model.md) and the
  HIPAA envelope in [03 §3](./03_tech_stack.md); it operationalizes them, it does not redefine
  them.

## 1. Entity + license obligations

Compliance-lawyer duty calendar (all cadences and thresholds **verify — counsel** against
**ACJA § 7-209** as amended):

| Obligation | Cadence | Owner |
|---|---|---|
| Compliance audits | Semi-annual | Compliance lawyer |
| License renewal + fees | Annual (license runs ~2 yr, renewable) | Compliance lawyer |
| Compliance Lawyer change notice | Within 30 days of change | Compliance lawyer |
| Ownership-change filings (**>10% = Authorized Person background checks**) | On any qualifying change | Managing attorney + counsel |
| Professional-liability insurance reporting | Per license terms | Compliance lawyer |

- **License artifacts** (certificate, application record, audit reports) stored in a
  counsel-designated, access-controlled location; **who signs** each filing is named in the
  duty calendar (managing attorney unless counsel directs otherwise) **(verify — counsel)**.

## 2. Attorney independence protocol (Rule 5.4(c) evidence)

The structural point from [07 §3](./07_captive_firm_model.md): the software prepares and
gates; **attorneys decide.** The **G1–G3 gate records are the audit evidence** that a
non-lawyer did not direct the practice of law.

| Decision | Who decides |
|---|---|
| Legal judgment | **Attorneys only** |
| Settlement advice + authority | **Attorneys only** |
| Case acceptance / rejection | **Attorneys only** |
| Pleading + demand content | **Attorneys only** |
| Disbursements | **Attorneys only** |
| Aggregate metrics (throughput, cycle time) | NewCo / investors / Bao **may view** |
| Direct a case decision or intake accept/decline | **Nobody outside the firm's attorneys** — never NewCo/investors/Bao-as-owner |

- **Software's role:** prepares work product and gates it; it **cannot ship a demand an
  attorney did not approve** (the G3 sign-off) — this is the [07 §3](./07_captive_firm_model.md)
  5.4(c) argument, kept true operationally.
- **Annual independence attestation** signed by the managing attorney.
- **Any pressure incident** (an owner/investor attempting to direct a case decision) is
  **documented + escalated to counsel** immediately.
- **Control-creep review** ([07 §6](./07_captive_firm_model.md)) every **6 months** against
  the MSA/license terms — the formally-compliant-MSA-drifts-into-de-facto-control failure mode.

## 3. Intake + solicitation

- **AZ solicitation baseline (verify — counsel):** no in-person / live solicitation of
  accident victims; advertising rules; trade-name rules — all per current Arizona authority.
- **No pay-per-signed-case lead arrangements** without counsel sign-off (the barratry /
  fee-split rail from [07 §6](./07_captive_firm_model.md)).
- Attorney-referral fee arrangements **papered per AZ rules (verify — counsel)**.
- **Intake scripts** approved by the managing attorney before use.
- **Declined-matter log** with reason for every decline (feeds the §8 solicitation-source
  review and shows intake decisions rest with attorneys).
- **Conflicts check before the engagement letter.**
- **Engagement letter + fee agreement** (contingency standard — **33⅓% pre-litigation
  (verify — counsel)** — with required disclosures); template **owned by counsel**.

## 4. Client communication duties

All timing rules **(verify — counsel)** against Arizona's rules of professional conduct:

- **Status updates:** every **30 days minimum**, and at each gate.
- **Settlement offers communicated in writing within 24h** of receipt **(verify — counsel on
  the specific timing rule)**.
- **Demand amount + strategy approved by the client before the G2.5 send** — the client
  authorizes the number, not the software.
- **Closing / disbursement statement** itemized, generated **deterministically from the
  money engine** ([05 M5](./05_implementation_plan.md)) — no hand math on client funds.
- **Client file access + return policy** stated in the engagement letter **(verify —
  counsel)**.

## 5. Malpractice + incident handling

- **Coverage:** per-claim / aggregate minimums set with the broker; **tail coverage on any
  attorney departure** — all limits **(verify — counsel + broker)**.
- **Incident = any of:** a missed deadline, an **unanchored fact discovered post-send**, or a
  **wrong-amount demand.** On any incident:
  1. Immediate **append-only audit entry** ([platform_core §3](./components/platform_core.md)
     `AuditEvent`).
  2. **Managing attorney + counsel notified.**
  3. **Carrier notice** per policy rules **(verify — counsel on notice timing)**.
- **Near-miss review feeds Tier-1 evals** — a caught unanchored fact or wrong number becomes a
  deterministic check, closing the loop between practice incidents and the eval suite.

## 6. Corporate separateness hygiene

The deep-pocket / veil-piercing defense from [07 §6](./07_captive_firm_model.md), run as
routine hygiene:

- **No NewCo employee performs legal work** — the bright line.
- **Separate bank accounts + books** for firm and NewCo; **no commingling.**
- **License + MSA invoices at documented FMV**, paid on schedule (the Route-A discipline that
  keeps this out of Route-C territory — [07 §2](./07_captive_firm_model.md)).
- **Separate board / manager minutes.**
- **Shared-staff time allocated + billed** at FMV.
- **Annual separateness checklist review by counsel.**

## 7. PHI / HIPAA SOP

The operational version of [03 §3](./03_tech_stack.md) — the paper rules turned into daily
conduct:

- **BAA inventory current before any new tool touches PHI** (the [03 §3](./03_tech_stack.md)
  rule 1 gate; see also [11 §4](./11_spike_briefs.md)).
- **Minimum-necessary, role-scoped access** (`paralegal` / `attorney` / `admin` roles from
  [platform_core §1](./components/platform_core.md)).
- **All page access logged** — the `phi_access` `AuditEvent`
  ([platform_core §3](./components/platform_core.md)).
- **Third-party-patient pages flagged → redact/exclude before binder build** (the
  `third_party_phi` risk flag — [03 §3](./03_tech_stack.md) rule 6).
- **Breach protocol:** internal escalation within **24h**, then **counsel-guided
  notification analysis (verify — counsel)** — the firm does not self-determine breach
  notification.
- **Retention / deletion** per engagement letter **and legal holds** (the G8 hold semantics —
  [platform_core §8](./components/platform_core.md) open Q3).
- **Offshore staff:** case-support access **only** under **BAA-equivalent contractual
  safeguards + training (verify — counsel)**; **engineers are fixtures-only** with no PHI
  access by design ([11 §4](./11_spike_briefs.md) EOR boundary).

## 8. Audit + records cadence

| Cadence | Reviews |
|---|---|
| **Monthly** | Audit-log spot review (gate overrides, PHI-access anomalies); budget/meter review ([platform_core §1](./components/platform_core.md)) |
| **Quarterly** | Declined-matter + intake-source review (solicitation compliance, §3); rules-pack YAML re-verification sampling ([05 M3](./05_implementation_plan.md)) |
| **Semi-annual** | ABS compliance-audit prep (checklist); independence attestation (§2); control-creep review ([07 §6](./07_captive_firm_model.md)) |
| **Annual** | License renewal; insurance renewal; **runbook re-approval by counsel** |

## 9. Change control + escalation

- **Counsel review BEFORE action** on any of: a deviation from this runbook, a new marketing
  channel, a new vendor touching PHI, or any fee-structure change. These are the
  highest-risk moves ([07 §6](./07_captive_firm_model.md) regulatory + control-creep risks) —
  none is self-serve.

| Escalation contact | Role |
|---|---|
| Managing attorney | First-line owner of firm decisions + this runbook |
| Compliance lawyer | License duties (§1), independence (§2) |
| Outside ethics counsel | Structure, 5.4(c), solicitation, breach notification |
| Malpractice broker | Coverage, tail, carrier notice (§5) |

- **Runbook version history** appended below on every counsel-approved change (date, section,
  change, approver).
