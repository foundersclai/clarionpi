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

## 5. The architectural edge — the TMEPAgent lineage

- **Added:** 2026-07-20 · architecture-edge analysis (design reasoning + repo grounding),
  distinct from the §1–§4 web recon (2026-07-03). Competitor claims below reuse the §1
  findings and their stated confidence — the "no page-level provenance" gap remains a *soft
  signal* (see §1 weakness 2), not a confirmed non-feature.

**Thesis:** Everyone else — EvenUp, Supio, Parrot, Filevine — built AI that **writes demand
letters**. The architecture inherited from TMEPAgent builds a **verified system of record**;
the letter is a deterministic *rendering* of it. That is a difference in kind, not in draft
quality — and draft quality is the axis they win on (data flywheel + 100–150 reviewers).

**Why the lineage transfers.** TMEPAgent was built for trademark prosecution: drafting
against an *adversarial, codified reader* (the USPTO examining attorney), where every
assertion must trace to selected evidence and cited authority or it fails. PI demands have
the **same adversarial-reader structure** — the adjuster, defense counsel, and behind them
the firm's malpractice carrier — but the market treats demand generation as mail-merge with
an LLM. The edge is importing prosecution discipline into a domain that never demanded it.

**Mechanisms → the incumbent's documented failure modes.** Each row attacks a weakness §1
already records for EvenUp:

| Our mechanism (this repo) | Makes structurally impossible | Their answer to the same problem (§1) |
|---|---|---|
| **Tokenize-or-omit** — `[[FACT]]`/`[[AMT]]` spine, renderer substitutes `display_form` verbatim, orphan → SENTINEL → G3 block | The model *cannot express* an uninvented fact or dollar in the letter — fabrication is unrepresentable, not caught-after | 100–150 human reviewers; their reply to the 2024 hallucination reports was "review catches it," not "the AI is accurate" (§1 weakness 1) |
| **Deterministic money engine** — integer cents, derived ledger, hash-pinned `[[AMT]]`, dedup-before-sum | Double-counted bills, totals that don't tie, silent post-edit drift (surfaces as "ledger drift" *on the figure*) | Reviewed spreadsheets; no published money invariants |
| **Page-level provenance as the spine** — every fact → source page; computed totals decompose to their bill lines | Un-auditable claims; "where did this number come from?" has no answer | Their conspicuous gap — no click-to-source-page (§1 weakness 2, soft signal) |
| **In-firm HITL gates** — atomic approve-with-edits, versioned plans, idempotency, audited overrides | Un-attributable approvals; the audit trail *is* the workflow (malpractice-defensible) | Vendor-side review: their staff, their black box, the firm's liability |
| **Plan-as-contract (G2.5)** — attorney approves what the letter may argue *with* before drafting; edits re-emit the plan | Steering by red-lining prose after generation | Draft-then-edit, like everyone |

Underneath these: rule packs pinned per matter by fingerprint (SOL/deadline math — the actual
malpractice surface — as lawyer-audited data, not prompt seasoning), byte-deterministic
packages with continuous Bates (certainty about *what was sent*), and the LLM treated as the
least-trusted component — one metered choke point, fail-visible degradation.

**Why it's an economic edge, not only an engineering one.** EvenUp's hallucination control is
*headcount* — a services-margin problem PLAAS deepens (legal-ops Glassdoor 1.8/5, §1 weakness
4). Ours is *invariants* — near-zero marginal verification cost per demand, and the firm's own
attorney at the gate instead of a vendor's contractor. Their moat (outcomes dataset, $385M,
distribution) is real and we do not neutralize it — but it does not neutralize ours, because
ours attacks their **documented failure mode: trust.** Trust in legal work is asymmetric: one
fabricated injury in one letter costs a firm more than a year of saved drafting time. In the
captive-firm model this compounds — provenance and audit are not a sales feature, they are how
our own firm survives its own carrier audit ([07_captive_firm_model.md](./07_captive_firm_model.md)).

**What the architecture does *not* buy (honest bounds).** Not extraction quality (same
foundation models — e.g. a bill stating only a service *period* with no per-line date once broke a
too-strict schema, observed and fixed 2026-07-20, see
[../planned/extraction_confidence_roadmap.md](../planned/extraction_confidence_roadmap.md)),
not their data flywheel, not their distribution. The design never claimed extraction
perfection — it claims **extraction failure cannot be silent** (the EC roadmap closes the one
gap that claim had). That yields the one sentence EvenUp structurally cannot say, because for
them verification is their staff's job and for us it is the substrate:

> *"Nothing reaches your demand letter without either a page citation or your own hands on it."*
