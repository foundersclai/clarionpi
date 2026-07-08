# PI Agent — Competitive Landscape

- **Status:** Research-grounded (web recon 2026-07-03) · sources dated inline
- Anchor competitor: **EvenUp**. Everything else is context. The strategic conclusion this
  doc supports: don't fight EvenUp for SaaS seats — own the demand side via the captive
  firm ([07_captive_firm_model.md](./07_captive_firm_model.md)).

## 1. EvenUp — verified snapshot (as of 2026-07)

### Scale & capital

| Metric | Figure | Source (date) |
|---|---|---|
| Total raised | **$385M**; Series E $150M at **$2B+ valuation** | Company + Fortune (2025-10-07) |
| Customers | 2,000+ firms; 20–30% of top-100 PI firms | Series E release (2025-10-07) |
| Volume | ~10,000 cases/week; 200K+ cases resolved; "$10B damages secured" | Series E release (2025-10-07) |
| Headcount | ~670–850 (third-party estimates) | PitchBook/Tracxn (2026) |
| Human review staff | 100–150+ nurses, paralegals, adjusters, lawyers | Company Piai page (2025-26) |
| Strategic investor | REV (RELX / LexisNexis VC arm) in Series E | Series E release (2025-10-07) |

### Product timeline (their shipping cadence is the real signal)

| When | What |
|---|---|
| Core | Expert Demands (AI draft + vendor-side human review, 1–5 day turnaround), Medical Chronologies |
| Oct 2024 (Series D) | Case Preparation (missing-docs detection), Negotiation Preparation, Executive Analytics, Settlement Repository |
| Jan 2025 | **Express Demands** — self-serve, minutes, down-market move |
| May 2025 | AI Drafts Suite, Smart Workflows, Medical Bill Summary, **case-based pricing** |
| Dec 2025 | **Medical Management** — real-time treatment monitoring, gap flagging, bilingual SMS/voice client check-in agents |
| 2026 | Companion (firmwide cross-caseload Q&A), Mirror Mode (firm-style cloning) |
| May 2026 | **PLAAS** — staffed pre-litigation-as-a-service: records retrieval, care coordination, demand prep, carrier negotiation; >$10M early subscriptions |

Read the trajectory: down-market (Express), upstream into treatment (Medical Management),
and **into the firm's operations entirely (PLAAS)**. They are becoming an operations
company, not just a drafting vendor.

### Piai + compliance posture

- "Piai" branding: proprietary models trained on claimed **100K+ PI cases / 1M+ medical
  records**; human review marketed as the hallucination control. No published accuracy or
  error rates.
- SOC 2 Type II (recertified) + HIPAA attestation; public trust center; claims case-level
  data separation and zero-retention processing.

### Documented weaknesses

1. **Hallucination reporting** (Business Insider, 2024-12-13, via secondary mirrors):
   former employees described missed injuries, fabricated medical conditions, inaccurate
   visit records; staff told not to trust the internal AI; company response was that human
   review catches errors — not that the AI is accurate. No newer incident found, but no
   accuracy metrics published either.
2. **No per-fact, page-level provenance.** No product page claims click-to-source-page;
   third-party reviews note firms can't see how facts were extracted or ranked. *(Soft
   signal — inference from absence + one review, not a confirmed non-feature.)*
3. **Opaque commercial motion:** no public pricing, no trial, 4–8 week sales cycle
   (third-party estimates $500–2K/mo, enterprise contracts, 200+ case firms targeted).
4. **Services economics + morale:** legal-ops Glassdoor 1.8/5 (8–11% recommend);
   offshoring reports; the human layer is a margin problem PLAAS deepens.

## 2. Everyone else (context tier — not verified this session unless noted)

| Player | What they are | Note |
|---|---|---|
| Supio | AI case-file analysis + demands, PI-focused | Well-funded fast follower (figures unverified) |
| Parrot | Demands/records for PI | Smaller point solution |
| Eve | Plaintiff-firm AI platform | Broader-than-demands positioning (figures unverified) |
| Filevine (DemandsAI, MedChron) | Case-management incumbent with in-house AI | **Channel competitor** — owns the workflow surface; competes with EvenUp too |
| DigitalOwl, Wisedocs | Medical record summarization | Component vendors, not demand products |
| **Justpoint** | Tech co + **captive AZ ABS mass-tort firm** (approved 2025-07) | The structural competitor — validates the captive-firm model; $105M raised; avg claimant payout $355K (Law.com 2026-02-11) |

## 3. Gap analysis — them vs this design

**They have, we don't (structural):** outcomes dataset that compounds 10K cases/week;
$385M + distribution (2,000 firms, CMS integrations, LexisNexis on the cap table);
a 100–150-person review workforce sold as done-for-you service (PLAAS); upstream treatment
monitoring; years of OCR/extraction scar tissue; SOC 2 track record + references.

**We have, they don't (by design):** per-fact page-anchored provenance as the product
spine (their conspicuous gap); tokenize-or-omit anti-fabrication with published invariants
(their weakness is documented); deterministic money engine; in-firm HITL gates with audit
(vs vendor black box); software margins.

**The strategic conclusion:** selling seats against them is a war of attrition we lose.
Two viable postures existed:
1. Narrow SaaS: "the auditable demand" for provenance-sensitive firms — viable but slow,
   and Express Demands erodes the down-market opening.
2. **Captive firm (chosen direction):** stop selling picks and shovels into their gold
   rush; use the software to run our own mine. EvenUp's own PLAAS launch and Justpoint's
   ABS approval both point the same way — the value pools in PI are **operations and case
   economics**, not software seats. See [07_captive_firm_model.md](./07_captive_firm_model.md).

## 4. What we deliberately copy from their playbook

- Treatment-gap intervention *upstream* (their Medical Management) — natural in a captive
  firm where we run intake and client comms; feature H6 in [02_feature_list.md](./02_feature_list.md).
- Provenance report elevated to MVP (E4) — our counter-positioning artifact, promoted from
  v1.x after this analysis.
- Their pricing opacity → our transparency (when/if we license the software outbound).
