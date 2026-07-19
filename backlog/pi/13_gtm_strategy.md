# PI Agent — GTM Strategy (Captive Firm)

- **Status:** DRAFT for founder review · **Date:** 2026-07-19
- **Research basis:** multi-agent web sweep with adversarial verification against primary
  sources, run 2026-07-18/19, layered on the 2026-07-03 recon in
  [06](./06_competitive_landscape.md)/[07](./07_captive_firm_model.md). Sources dated
  inline; confidence tags: **[verified]** = checked against the primary document this run,
  **[primary]** = primary source, extracted but single-pass, **[reported]** = credible
  secondary, **[weak]** = vendor/marketing figure, use as bound not input.
- **Scope:** operationalizes the adopted captive-firm GTM ([07](./07_captive_firm_model.md))
  into a case-acquisition plan, and partially closes **B3**
  ([10 §2](./10_implementation_readiness.md)). The remaining B3 close is the §11 operator
  sprint; [08 §3](./08_seed_plan_and_budget.md)/[09 §2](./09_bootstrap_abs_path.md) numbers
  get revised only after those interviews. Nothing here reopens the structure decision.
- ⚠️ Every marketing/intake mechanic below is subject to the
  [runbook §9](./12_abs_ops_runbook.md) rule: **counsel reviews any new channel before
  it goes live.** This doc plans channels; it does not authorize them.

## 1. The one-sentence answer

Win Phoenix MVA cases by being the **fastest, most transparent firm a referrer or client
can hire** — a referral-network backbone plus an LSA-anchored, bilingual, <5-minute
intake machine — and refuse the billboard/TV arms race entirely; the researched numbers
say the existing [08 §3](./08_seed_plan_and_budget.md) case model survives contact
(fee ~$7K conservative-to-fair, CPA $1,100 plausible but wide-banded at $600–2,500),
with intake conversion and review velocity, not ad spend, as the levers that decide it.

## 2. The market, verified

| Input | 2024 value | Source · confidence |
|---|---|---|
| Maricopa County total crashes | **88,094** (72.74% of AZ) | ADOT 2024 Crash Facts, Table 2-2 · **[verified 3-0]** |
| Maricopa injury crashes | **25,990** (3-yr trend: 24,681 → 25,456 → 25,990) | same · **[verified 3-0]** |
| Maricopa persons injured | **37,649** | same · **[verified 3-0]** |
| City of Phoenix injury crashes / injured | 10,449 / 15,057 (Mesa 2,503, Tempe 2,049, Glendale 1,456, Chandler 1,263, Scottsdale 1,421 injury crashes) | same, Table 2-5 · **[verified 3-0]** |
| Vehicle-vs-vehicle share | ~78% of crashes, ~80% of injured persons statewide | same, Table 2-1 · **[primary]** |
| Regional economic loss | $10.47B (MAG region, 2024) | MAG Crash Trends · **[reported]** |

- **Wedge validation:** vehicle-on-vehicle collisions are ~80% of injured persons — the
  MVA-only scope guard ([00 §3](./00_vision_and_scope.md)) covers most of the pool.
- **Feasibility check:** ~37.6K injured persons/yr in Maricopa. Attorney-retention share
  is the one unresearched rate (IRC publishes attitudes, not retention — **[gap]**; use a
  25–50% scenario band → ~9–19K represented claimants/yr). The plan's year-2 target of
  ~230 signed cases ([08 §3](./08_seed_plan_and_budget.md)) is **~1–2.5% of the
  represented pool** — small enough that share is not the binding constraint; acquisition
  cost and conversion are.
- Pool is stable, not shrinking: injury crashes rose three straight years even as
  fatalities fell. A 2025-data ADOT edition is due ~now (annual July cadence) — check on
  release. · **[primary]**

## 3. Unit-economics validation (what the research did to 08 §3 / 09 §2)

| Assumption | Plan value | Research says | Verdict |
|---|---|---|---|
| Avg pre-lit MVA settlement | $21K → **~$7K fee** | US avg BI liability paid claim **$28,278** (2024, all claims incl. unrepresented) **[verified]**; ~half of PI cases resolve ≤$24K (Forbes) **[reported]**; median auto settlement ~$23.9K, survey mean $29.7K w/ injuries **[reported]**; BI severity +~8%/yr 2020→24 **[verified]** | **Holds, conservative side.** Keep $7K base, $5K bear / $9K bull ([09 §9](./09_bootstrap_abs_path.md) grid unchanged). Tailwind: severity inflation. Drag: 25/50 policy-limits clustering (below). |
| Blended CPA | **$1,100** (guardrail $1,500) | PI LSA leads $195–250 **[reported]**; PI LSA lead→retained ~34% → ~$630–735/signed case **[weak — vendor]**; independent $3.3M-spend study: LSA $2,485/signed, Google Ads $2,971/signed, blended across practice areas **[reported]**; digital legal ad costs +84% spend on −50% volume 2020→24 **[primary]** | **Plausible, wide band ($600–2,500).** $1,100 is defensible mid-case *if* intake converts well and reviews accumulate; neither confirmed nor killed by desk research → **B3 interviews decide** (§11). Guardrail $1,500 and kill $2,000 stand. |
| Lead→signed conversion | (implicit) | PI overall inquiry→signed **10–25%**; consult→signed 20–35%; LSA-sourced ~34% retained **[reported]**; avg firm converts 14%, top firms 40–50% **[reported]** | **New explicit input.** Plan at 20% blended qualified-inquiry→signed; §6 targets ≥25%. |
| Sign-to-collect | ~7 months | Simple pre-lit claims settle 3–6 months from injury; complex 12–24 **[reported]** | **Holds.** Software's demand-speed edge attacks the middle of it. |
| Contingency norms | 33⅓% pre-lit | 33% pre-suit / 40% post-filing standard **[reported]**; funded-ABS undercutter exists (Mayfair Legal at 22%, Nera Capital) **[reported]** | **Holds**; note fee-pressure entrants (§9). |
| AZ structural facts | — | Min BI limits **25/50/15** since 2020-07-01 (A.R.S. 28-4009); ~**11–13%** uninsured drivers (IRC/NAIC range); UM/UIM must be offered, rejection in writing (A.R.S. 20-259.01); 2-yr SOL; at-fault state | **[primary]** New model inputs: a meaningful minority of cases cap at $25K limits (supports the conservative $21K avg) or route to UM/UIM against the client's own carrier (still fee-able). |

**Net:** the case model's two decisive numbers ($7K fee, $1,100 CPA) came through desk
research intact — one validated, one bounded. The flywheel risk moved: it is **not** ad
price, it is **conversion mechanics** (speed-to-lead, review velocity, bilingual
coverage) — all buildable, all measurable, and all in §§5–8.

## 4. Positioning — the firm consumers and referrers hire

Phoenix PI advertising is a saturated shouting match (§9). The counter-position mirrors
the product thesis: **the fast, transparent firm.**

- **Client promise:** *"We move: demand out in days once treatment ends, a status update
  every 30 days minimum, and you approve the number — nothing is sent without you."*
  Speed and the client-approval gates ([12 §4](./12_abs_ops_runbook.md)) become the
  consumer brand, not AI. (Consumer AI-branding is a liability in injury services;
  the software is the *how*, never the pitch.)
- **Referrer promise (§8):** *"Your client is signed within hours, worked immediately,
  and you see the milestones."* Speed-as-reputation is the referral moat
  ([09 §6](./09_bootstrap_abs_path.md)) — the referring lawyer keeps sending because
  ours close fast and clean.
- **Proof artifacts, not claims:** published cycle-time stats once real ("median X days
  record-complete → demand out"), review velocity (§7), and the 30-day-update discipline.
  ER 7.1 rails: no misleading claims, no guaranteed results, past-results disclaimers,
  every ad names the responsible lawyer + contact info · **[primary — azbar.org]**.
- **Bilingual from day 1.** Spanish-language intake is a first-class surface, not a
  translation afterthought (~31% of Maricopa identifies Hispanic/Latino — **(verify —
  census pull)**; Los Defensores was the **#3 legal advertiser in the US in 2024 at
  ~$48.7M** **[primary — ATRA/Vivvix]**, proving the demand channel). Naming/brand and
  Spanish-market creative are a single decision — pick a name that works in both
  languages.
- **What we are not:** a billboard brand. Dimopoulos entered Phoenix in 2025 with a
  pure-brand "WE WIN" OOH blitz (no phone number, no practice description) **[reported —
  Phoenix New Times 2025-09]** — that is the capital-intensive game
  [09 §4](./09_bootstrap_abs_path.md) refuses. We convert intent; we don't manufacture
  awareness.

## 5. Channel plan — phased, gated, priced

Phases key to the ABS/license timeline ([08 §5](./08_seed_plan_and_budget.md)) and
release stages ([10 §3](./10_implementation_readiness.md)). Every channel passes
[runbook §3/§9](./12_abs_ops_runbook.md) counsel review before spend.

### Phase 0 — pre-license (now → ABS license; software R0–R1)

No practice, no client marketing. Build the machine:
brand + bilingual site + intake stack selection (§6), Google Business Profile shell,
review-capture workflow, content/local-SEO seeds (AZ crash-guide pages), referral
target list + first-degree warm-ups (§8 — relationship building only; ER 7.3 expressly
permits soliciting *lawyers*), LSA application prepped to activate on licensure
(LSA requires license verification). **Budget ≈ $0 media; founder time + site build.**

### Phase 1 — license → month ~6 of operations (R2)

| Channel | Mechanics | Expected economics | Kill/pause rule |
|---|---|---|---|
| **Attorney referrals** (backbone) | 20–40 relationships: immigration, family, criminal, workers'-comp (§8) | CPA ≈ $0 cash; net fee ~$4.7–5.25K after 25–33% referral fee ([09 §2](./09_bootstrap_abs_path.md)) | n/a — this channel is never paused |
| **Google LSA** (paid anchor) | On from week 1; bilingual profiles; review velocity program (§7) feeds rank | $195–250/lead, target ≥30% lead→retained → **$650–850/signed** at maturity; model at $1,000 early (thin reviews) | Pause if trailing-4-wk CPA > $1,500 **and** review count < 30 |
| **Google Ads (search)** | Exact-match, Spanish + English, high-intent only ("phoenix car accident lawyer" class); LSA overflow | ~$95–250/lead but ~5% paid-search lead→retained is the trap **[reported]**; expect $2,000–3,000/signed → **small tester budget only** | Kill if CPA > $2,000 for 8 wks |
| **GBP / local SEO / reviews** | Weekly content, review velocity §7 | Compounding, ~$0 marginal | n/a |

Target exit of Phase 1: **5–8 signed/mo**, ≥50% referral-sourced, blended cash CPA ≤ $900.

### Phase 2 — months ~6–18 of operations (R2→R3)

- Scale LSA spend against measured CPA; expand Google Ads only where LSA is
  impression-capped.
- **Niches before brand** ([09 §6](./09_bootstrap_abs_path.md)): Spanish-language
  full-funnel (dedicated landing + intake + community presence), rideshare/delivery
  drivers (contested-coverage niche the volume shops under-serve), motorcycle.
- **Lead-gen/aggregator test (counsel-gated):** ER 7.2 permits paying lead generators
  that don't *recommend* the lawyer or imply claim analysis **[primary — azbar.org]**,
  and post-2021 Arizona uniquely allows paying referral fees to non-lawyers
  **[primary]** — but [runbook §3](./12_abs_ops_runbook.md) bars pay-per-signed-case
  deals without counsel sign-off, A.R.S. § 13-2924 criminalizes compensated tort-victim
  solicitation, and AO 2026-31 exists precisely because lead-gen-shell ABSs drew fire
  (§15). Structure any test as flat-fee, per-lead, TCPA-clean, counsel-papered.
- Radio/OOH: **VC track only**, as a measured test — Phoenix's legal-ad market skews
  OOH+radio over spot TV (top-10 nationally in both; absent from TV top-10)
  **[primary — ATRA]** — bootstrap track stays out entirely.

### Phase 3 — month 18+ (R3+)

Hold the §12 dashboard at target, then choose: compound the niches (bootstrap plateau,
[09 §4](./09_bootstrap_abs_path.md)) or fund the second acquisition wave (VC track).
TV/billboard brand remains out unless a Series-A-scale decision reopens it.

## 6. The intake engine — where the model is won or lost

The research's clearest exploitable gap: **42% of law firms take 3+ days to respond to
inquiries** (Clio 2025 Legal Trends) **[reported]**, while first-responder-wins and
5-minute-response effects dominate conversion (directionally solid, vendor-quantified
**[weak]**), and PI intake converts 10–25% inquiry→signed vs 40–50% for top operators.

- **Standard: first human response < 5 minutes, 24/7, English + Spanish.** Offshore
  overnight coverage per [08 §2](./08_seed_plan_and_budget.md) (BAA-equivalent
  safeguards, counsel-gated); managing-attorney-approved scripts only
  ([12 §3](./12_abs_ops_runbook.md)).
- **Attorneys decide acceptance** — intake qualifies, never accepts
  ([12 §2](./12_abs_ops_runbook.md)); the declined-matter log doubles as the §12
  conversion dataset.
- **Instrumented in ClarionPI, not a side spreadsheet:** source attribution on every
  inquiry (channel → signed → resolved → collected), speed-to-lead timestamps, decline
  reasons. Buy-vs-build: start with a PI intake CRM (Lead Docket-class) integrated to
  the matter pipeline; revisit build once volume justifies it.
- **Targets:** ≥25% qualified-inquiry→signed by month 6 of operations; p50 response
  < 5 min; 100% of inquiries source-attributed; zero un-logged declines.
- **TCPA discipline (hard rail):** written consent for texts/calls, no AI outbound
  calls, no purchased accident lists. This is live enforcement, not theory: a July 2026
  Texas class action targets mass-tort firms over AI-generated solicitation calls
  **[reported — Law360 2026-07-13]**, and the Arizona Republic investigation found ABS
  licensees accused of robocall/auto-text abuse **[reported — 2026-02]**. Lawyers are
  ethically responsible for vendors' marketing conduct **[primary — azbar.org]**.

## 7. Review velocity — the LSA gating asset

BrightLocal click data: **review rating drives LSA clicks more than rank position**
(51% vs 17% of consumers; a 4.5★ listing in position 3 out-clicks a 3.6★ in position 1)
**[reported]**. A new firm cannot simply buy LSA volume — reviews gate it.

- Every resolved matter triggers a review ask (built into the closing workflow, next to
  the deterministic disbursement statement — the moment satisfaction peaks).
- Referral-sourced early cases (§8) resolve first and seed the first ~25 reviews before
  LSA spend scales — the cold-start answer.
- Targets: 25 Google reviews by month 6 of operations, 75 by month 12, sustained ≥4.8★.
  No incentivized reviews (platform + ER 7.1 rails).

## 8. The referral network — backbone channel, made concrete

Mechanics per [09 §6](./09_bootstrap_abs_path.md), now operationalized:

- **Build list:** 100 candidate referrers across immigration, family, criminal,
  workers'-comp, employment; prioritize solo/small firms with Spanish-language
  practices. ER 7.3 permits direct solicitation of lawyers and business relationships
  **[primary]**.
- **Offer:** 25–33% referral fee, papered per AZ rules (client consent in writing;
  post-2021 Arizona fee-division rules are permissive — exact papering is
  **(verify — counsel)**, [12 §3](./12_abs_ops_runbook.md)); a named intake line
  (referrer's client answered by name); milestone visibility (signed → records-complete
  → demand out → resolved).
- **Service SLA as the moat:** same-day signing of referred clients, weekly first-month
  updates back to the referrer. The compounding loop: fast, clean resolutions →
  referrer's client thanks *them* → next case arrives.
- **Non-lawyer referral sources** (body shops, chiropractors, community orgs): Arizona
  uniquely allows paying non-lawyer referral fees post-2021 **[primary]**, but
  A.R.S. § 13-2924 (compensated solicitation of tort victims = crime) sits directly on
  this line — **no compensated non-lawyer arrangements of any kind without counsel
  papering** ([12 §3](./12_abs_ops_runbook.md)). Uncompensated community presence
  (Spanish-language orgs, driver associations) is the safe version and starts in Phase 1.
- **Targets:** 20 active referrers by month 6 of operations, 40 by month 18;
  ≥50% of Phase-1 signings referral-sourced; referral share may fall below 50% only
  after blended paid CPA is proven ≤ $1,100.

## 9. Competitive read — Phoenix consumer market

| Player class | Facts | Implication |
|---|---|---|
| Mega-advertisers | Phoenix = **#8 US legal-ad market, ~$58.3M/558K ads (2024)**; Sweet James ~$44.7M national spend (~$18.7M spot TV); Lerner & Rowe ~$24.3M national/470K units **[primary — ATRA/Vivvix 2025-03]**; billboard field saturated by Rafi Law Group, Husband & Wife (Breyer), Lerner & Rowe **[reported]**; Dimopoulos (LV) entered 2025 with pure-brand OOH **[reported]** | Don't out-shout — out-convert. Their weeks-long demand cycles and call-center intake are the openings §4/§6 attack. |
| Spanish-language aggregators | Los Defensores #3 US legal advertiser (~$48.7M, 2024) **[primary]** | Validates bilingual demand; aggregator buying is counsel-gated (§5 Phase 2). |
| Funded ABS entrants | 153 ABSs approved (Oct 2025; 51 in 2024 alone) **[reported — Bloomberg Law]**; institutional capital in AZ tort ABSs (Benefit Street/Copper State) **[reported]**; fee undercutters (Mayfair 22%) **[reported]**; **Law Bear** — Phoenix PI ABS application in progress, AZ-native direct representation **[reported — 2026-04, still pending]** | Expect CPA inflation + fee pressure over time. Our defense is cost structure (software margins) and conversion, not price war. Track Law Bear as the closest structural twin. |
| Justpoint | Mass-tort/FDA-product focus (Oxbryta etc.), $105M, SignalFire **[reported]** | **Not an MVA competitor** — narrower overlap than [06 §2](./06_competitive_landscape.md) implied. Structural validator, not rival. |
| EvenUp (as firm-side arms dealer) | PLAAS: >$10M subscriptions; claims records 66 days faster, demands 47 days faster, 95% of available policy limits; 30% of top-100 PI firms **[reported — LawSites 2026-05]** | Incumbent firms are buying speed too. Our speed edge must come from owning the whole stack (intake→demand), not the demand doc alone — reinforces captive-firm logic. |
| Supio | **Post-07-03:** H1-2026 report (2026-07-13): 17x ARR growth, customer base doubled in H1, Supio Agent (May), exclusive Westlaw Advantage tie-in, Thomson Reuters Ventures investor **[reported — PR]** | The SaaS-seat war is fully armed on both sides — reconfirms [06 §3](./06_competitive_landscape.md): don't fight for seats. |

## 10. Dual-track software option (secondary)

- **Trigger, unchanged:** 1–2 independent firms licensing at FMV as a Series-A signal
  ([08 §7](./08_seed_plan_and_budget.md)); bootstrap track touches this only after R3
  stability. Until then the software's GTM *is* the firm's performance.
- **New regulatory caveat:** ACJA § 7-209(E)(1)(a) locks the ABS's **declared purpose**;
  changes need Committee approval (+$250) **[primary — code text eff. 2026-03-18]**.
  The *firm's* ABS purpose must be papered so a NewCo software-licensing dual track
  never depends on amending it (the licensing happens from NewCo, not the ABS — keep it
  that way; **(verify — counsel)** at B1).
- **Pricing posture when triggered:** transparent published per-demand/per-case pricing
  as the anti-EvenUp move ([06 §4](./06_competitive_landscape.md)). Benchmarks: EvenUp
  never publishes; competitor intel pegs legacy ~$750+/demand, per-case bundle since
  May 2025 **[weak — competitor-sourced]**; low-cost vendors exist at ~$250/demand flat
  (ApexDemands) **[weak]**. Our wedge is **auditability at a fair price**, not cheapest.
- **Proof artifact:** the E4 provenance report + published cycle-time/zero-unanchored
  stats from our own firm — sales collateral no seat-seller can copy.

## 11. Closing B3 — the operator-interview sprint

Desk research bounded the two decisive numbers; only Phoenix operators close them
([09 §9](./09_bootstrap_abs_path.md): the $5K-fee/$2K-CPA corner kills both tracks).

- **Who:** 8–12 structured interviews — 4+ Phoenix PI managing partners/operators
  (mix: referral-built solos, LSA-heavy small firms, one volume shop alum), 2+ intake
  managers, 2+ candidate referring attorneys (immigration/WC), 1 PI marketing agency
  with Phoenix accounts, 1 litigation-finance underwriter (case-value sanity).
- **The questions that matter:** realized avg fee on pre-lit MVA (not headline
  settlements) and share below policy limits; realistic blended CPA by channel and
  current Phoenix LSA lead costs; lead→signed rates and what breaks them; drop/referral-out
  rates; sign-to-collect actuals; referral-fee norms and appetite to send to a new ABS
  firm; attorney comp expectations (feeds B2); which channels they'd kill first.
- **Output:** interview write-up; revise [08 §3](./08_seed_plan_and_budget.md)/[09 §2](./09_bootstrap_abs_path.md)
  in place; mark B3 closed in [10 §2](./10_implementation_readiness.md). **2–4 weeks,
  <$5K.** This is the last cheap de-risking before Gate M-1 money moves.

## 12. Measurement — the GTM dashboard

Weekly (managing attorney + founders), monthly channel rebalance; all sourced from the
§6 instrumentation, cohorted by sign month:

| Layer | Metrics | Targets (by mo 6 of ops) |
|---|---|---|
| Acquisition | spend, leads, CPA by channel; referral count by relationship | blended cash CPA ≤ $1,100; ≥20 active referrers |
| Conversion | speed-to-lead p50/p95; qualified-inquiry→signed by channel; decline reasons | p50 < 5 min; ≥25% signed |
| Reputation | review count/rating/velocity; LSA impression share | 25 reviews, ≥4.8★ |
| Economics | contribution/resolved case (fee − CPA − servicing − COGS); sign-to-collect days | ≥ $4K; ≤ 210 days |
| Compliance | 100% source attribution; declined-matter log complete; zero TCPA/solicitation incidents ([12 §8](./12_abs_ops_runbook.md) quarterly source review) | zero exceptions |

Monthly rebalancing rule ([09 §6](./09_bootstrap_abs_path.md) generalized): rank
channels by trailing-8-week contribution per dollar; shift next month's marginal budget
to the top channel; apply §5 kill rules mechanically — no sunk-cost overrides without a
written founder decision.

## 13. GTM risks & kill criteria

| Risk | Signal | Response |
|---|---|---|
| CPA inflation (funded entrants, digital cost trend +84%/4yr) | blended CPA > $1,500 sustained 8 wks | Pause paid, rebalance to referral ([09 §6](./09_bootstrap_abs_path.md)); >$2,000 w/ fee <$5K by mo 15 = track-level kill ([08 §8](./08_seed_plan_and_budget.md)) |
| Review cold-start stalls LSA | <15 reviews by mo 4 of ops | Hold LSA at floor spend; push referral share + review asks; do NOT buy volume through a weak profile |
| Conversion below band | inquiry→signed <15% at mo 4 | Fix intake before adding spend (scripts, speed, bilingual coverage) — spend on a leaky funnel is the classic PI failure mode |
| Fee compression (undercutter ABSs) | market moves to <30% pre-lit norms | Hold 33⅓ + outrun on speed/service; revisit only with B3-refreshed economics |
| Solicitation/TCPA enforcement (live: 2026-07 AI-calls class action; AZ Republic ABS findings) | any complaint or demand letter | Prohibited-plays list is absolute (no AI outbound, no purchased accident lists, no runners, no uncounseled pay-per-case); incident → counsel same day ([12 §9](./12_abs_ops_runbook.md)) |
| AO 2026-31 nexus scrutiny | Committee inquiry | We are the intended shape — AZ clients, AZ staff, AZ marketing; keep §5 channels AZ-targeted, no national lead resale, ever |
| Referrer concentration | any referrer >25% of signings | Widen the §8 list before scaling that relationship further |

## 14. Next 90 days (GTM workstream only)

1. **B3 sprint (§11)** — start immediately; it feeds the funding decision and B2 comp
   design. Owner: founders + Bao.
2. **Counsel channel review** — hand §§5–8 + the prohibited-plays list to ethics counsel
   at M-1a engagement; every **(verify — counsel)** in this doc resolves there.
3. **Brand + bilingual naming decision** — blocks site, GBP, LSA prep; test with §11
   interviewees.
4. **Intake stack selection** (§6 buy-vs-build) — decision memo, 2 weeks.
5. **Referral target list v1** (100 names) + first 10 warm conversations (relationship
   only, pre-license).
6. **Dashboard schema (§12) into the product backlog** — source attribution +
   speed-to-lead timestamps land with the matter-intake work ([02](./02_feature_list.md)
   intake features), not bolted on later.

## 15. Changed since 2026-07-03 (corrections + updates for 06/07)

1. **CORRECTION — Illinois SB3812/HB5487 did NOT pass.** [07 §2](./07_captive_firm_model.md)
   says "passed both chambers 2026-05-31, on the Governor's desk." Official ILGA status:
   Senate Committee Amendment No. 1 re-referred to Assignments under Rule 3-9(a) on
   **2026-03-27** — stalled in committee, no floor vote, never sent to the Governor
   **[verified — ilga.gov, 2026-07-19]**. Regulatory-backlash temperature is one notch
   lower than 07 states; CO/CA/SC rails unchanged.
2. **ABS fee schedule discrepancy.** Current ACJA § 7-209(J) text shows Regular ABS
   **$9,000 initial / $6,000 annual** (+$300/day late, investigation pass-throughs)
   **[primary — code eff. 2026-03-18]**; [07 §2](./07_captive_firm_model.md) says
   ~$9K/yr renewal (H&K's Dec-2025 guide agrees with $9K/$9K). Counsel confirms at B1;
   immaterial to economics either way.
3. **Supio surge (post-07-03).** H1-2026 report (**2026-07-13**): 17x ARR growth,
   customer base doubled in H1-2026, Supio Agent launched May 2026, exclusive Westlaw
   Advantage integration, Thomson Reuters Ventures on the cap table. Update
   [06 §2](./06_competitive_landscape.md)'s "figures unverified" row.
4. **TCPA/AI-solicitation enforcement is live.** Class action filed **2026-07-13**
   (Texas federal court) against mass-tort firms over AI-generated solicitation calls —
   the §6 hard rail has a fresh case number behind it.
5. **Justpoint scope narrower than implied:** mass-tort/FDA-product harm focus, not MVA
   pre-lit — soften "structural competitor" to "structural validator" in
   [06 §2](./06_competitive_landscape.md).
6. **Watch item:** ADOT 2025 Crash Facts due ~July 2026 (annual cadence); MAG's
   "two-thirds of crashes" figure is stale — Maricopa alone is 72.74% **[verified]**.

## Appendix A — key sources

| Topic | Source | Date |
|---|---|---|
| Crash data | ADOT 2024 Motor Vehicle Crash Facts (azdot.gov, PDF) — Tables 2-1/2-2/2-5 | 2025-07-09 |
| BI claim severity | III Facts + Statistics: Auto insurance (iii.org) — ISO/Verisk severity table | live 2026-07-18 |
| Settlement distributions | Forbes Advisor settlement analysis; Martindale-Nolo reader survey | 2025–2026 |
| AZ insurance law | A.R.S. 28-4009; A.R.S. 20-259.01; AZ DIFI consumer guidance | current |
| LSA/PPC benchmarks | Pareto Legal PPC/LSA statistics ($3.3M managed spend study); LawSmiths/OptimizeMyFirm LSA figures; WordStream benchmarks | 2025–2026 |
| Conversion/intake | LEXGRO conversion aggregate; Clio 2025 Legal Trends (3+ day response stat); MyCase 17.6% | 2025–2026 |
| Phoenix ad market | ATRA / X Ante "Legal Services Advertising in the United States, 2020-2024" (Vivvix data) | 2025-03 |
| Phoenix competitive color | Phoenix New Times (Dimopoulos/billboard saturation) | 2025-09 |
| ABS regime | ACJA § 7-209 as amended (AO 2026-31); Arizona ABS Update blog; Holland & Knight ABS guide; Arizona Republic/LawSites investigation; Bloomberg Law (investor ABSs) | 2025-12 → 2026-04 |
| Marketing ethics | State Bar of Arizona ER 7.1/7.3 best-practices; A.R.S. § 13-2924 | current |
| Illinois bill | ilga.gov SB3812 bill status | verified 2026-07-19 |
| Competitors | LawSites (EvenUp PLAAS); Supio H1-2026 PR; Justpoint PR/Law.com; Law Bear PR; Law360 (AI-solicitation suit) | 2026-02 → 2026-07-13 |
