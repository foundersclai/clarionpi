# Sources & Costs — Getting More Real Cases

Research date: 2026-07-10. Everything in `samples/` cost $0. This doc records
the repeatable free recipes and the cost-ordered paid options for scaling to a
~20-case calibration set (and beyond).

## Free recipes (verified working)

### 1. CourtListener/RECAP — the demand-letter vein

Real demand letters enter the free archive mainly as **exhibits to Notices of
Removal**: a defendant removing a state PI case to federal court must prove
amount-in-controversy and attaches the plaintiff's demand letter. Search the
API anonymously (docket pages are WAF-gated; the API and the PDF storage are
not):

```sh
curl -s "https://www.courtlistener.com/api/rest/v4/search/?type=r&available_only=on&q=%22demand%20letter%22%20removal%20underinsured" \
  | python3 -c "import json,sys; [print(d['filepath_local'], '|', r['caseName'], '|', d.get('short_description'))
      for r in json.load(sys.stdin)['results'] for d in r['recap_documents'] if d.get('filepath_local')]"
# then: https://storage.courtlistener.com/<filepath_local>   (direct, ungated)
```

Productive query seeds: `"demand letter" removal`, `"policy limits demand"`,
`"settlement demand" exhibit underinsured`, court filter `court_id:gand`
(N.D. Ga.'s `1:23-mi-99999` placeholder docket aggregates many removed PI
cases). Note `available_only=on` — it hides PACER-paywalled stubs. Full-text
hits are biased toward already-free docs; the archive holds a small minority
of all PACER exhibits.

### 1b. County-targeted variant (verified 2026-07-10, yielded `case_files/nj_middlesex/`)

To mine one specific county, add its state docket prefix to the query — NJ
Law Division dockets are `<COUNTY>-L-<n>-<yy>` (Middlesex = `MID-L`):

```sh
curl -s "https://www.courtlistener.com/api/rest/v4/search/?type=r&available_only=on&court=njd&q=%22MID-L%22%20removal%20negligence"
# also productive: q="MID-L" "demand letter" / "settlement demand" / "policy limits" removal
# then probe sibling exhibits directly: .../gov.uscourts.njd.<id>.1.1.pdf, .1.2.pdf ... (404 = none)
```

Yield from one pass: 170 candidate dockets, 11 kept — incl. one complete
lifecycle packet (crash report → demand letter → complaint → answer,
*Henderson v. Crown Trucking*, MID-L-001382-19). UM/UIM and trucking removals
are the letter-bearing subset; plain MVA removals usually carry only the
complaint. This generalizes to any county whose PI cases get removed
(diversity): swap `MID-L` for the county prefix and `njd` for the district.

### 2. Free full-document state portals

- **Broward County, FL** (browardclerk.org) — anonymous free PDF viewing of
  civil filings post-2013. **Miami-Dade** similar (Advanced tier needs a
  notarized registration, still free). Florida's statewide standard means most
  FL counties behave alike — the richest free state-court document source.
- **New Jersey eCourts** (njcourts.gov/public/find-a-case) — free viewing of
  filed civil PDFs (Law Division case jackets, each page blue-stamped), but a
  **free one-time registration is now required** (the old anonymous case-jacket
  URL redirects to the portal login as of 2026-07). Search by party name or
  docket number; to find PI cases without names, harvest `MID-L-*` docket
  numbers from App Div opinions (Justia mirrors them) or from the removal vein
  above, then pull jackets. Jackets carry complaints, answers, arbitration
  awards (NJ mandatory arb for auto cases — real injury+award numbers), and
  occasional letter exhibits in UM/UIM and settlement-enforcement motions.
  Demand letters per se are pre-suit and mostly NOT in state jackets.
- **Arizona is NOT free remotely.** Maricopa Superior Court documents: free
  only at in-person terminals; remote = eAccess (paid, below). Plan on FL/GA
  federal-removal letters for volume and treat AZ specimens as premium buys.
- **California** — a strong source state; details in §2b below.

### 2b. California — verified source map (future-state reference)

Recon 2026-07-10. CA is not a v1 target (pilot is AZ), but it is the strongest
second-state *sourcing* candidate, so the verified map is recorded here. Nothing
below is a fixture — THE RULE applies.

Why CA is letter-dense on the free tier: a CA PI complaint may not state a
damages figure (CCP § 425.10(b)), so a removing defendant must prove
amount-in-controversy with other paper — usually the demand letter. The RECAP
removal vein (§1/§1b) returned **412 candidate dockets** across the four CA
districts (`cacd` 191, `cand` 151, `casd` 36, `caed` 34); it already yielded
`major_v_zimprich_CA_policy_limits_demand.pdf`. Bonus: CA complaints are often
the Judicial Council fill-in form **PLD-PI-001** — a clean, field-based layout.

Genuinely free and useful (verified by direct fetch):
- **CA Supreme Court briefs** (supreme.courts.ca.gov) — full briefs for cases
  argued since 2010, self-hosted. (The *Courts of Appeal* host no briefs —
  earlier lead corrected.) SCOCAL (Stanford) + LA Law Library add older ones.
- **CHP Crash Investigation Manual** HPM 110.5 (chp.ca.gov) — decodes the
  CHP-555 collision-report fields. Blank **CHP-555** only via NHTSA mirror
  (`nhtsa.gov/.../ca_chp555_sub6_2012.pdf`), not CHP's own site.
- **Crash DATA** — TIMS/SWITRS (tims.berkeley.edu, free reg, ~10-yr window) and
  the successor **CCRS** (data.ca.gov/dataset/ccrs, no login, public domain,
  2016–2026). Aggregate tables, not individual reports (reports are
  parties-only, Veh. Code § 20012). Caveat: CCRS had data-quality/matching
  complaints during the Jan-2025 SWITRS-portal retirement — spot-check freshness.
- **HCAI/CHHS hospital chargemasters** (data.chhs.ca.gov/dataset/chargemasters)
  — aggregate, PHI-safe billed-charge data. **License caveat: Terms of Use are
  noncommercial-only; a commercial (for-profit) product needs OPA approval.**
  This licensing constraint — plus the file's size (2025 ZIP ≈ 989 MB) — is why
  we did *not* pull a chargemaster for bill realism; the real-redacted bills +
  the captured letters' specials tables already cover billed magnitudes.

The § 999 hook: CA's time-limited policy-limits demand statute (CCP §§ 999–999.5,
eff. 2023) prescribes required letter *contents* — the one state where demand
compliance is machine-checkable, i.e. a natural `ca.yaml` + `missing_statutory_term`
target if CA ever becomes a real jurisdiction.

Not free — CA county civil documents (each pulled by case number; CRC 2.503(f)/(g)
**bars bulk document pull**, so no scraped-corpus path exists):
- Per-page purchase, imaged civil cases: **LA** ($1/pg→$0.40, $40 cap),
  **Alameda** (same, $40 cap), **San Diego** ($1→$0.40, $50 cap), **Riverside**
  ($1→$0.50, $50 cap), **Orange** (online rate unconfirmed).
- **Index-only remotely** (documents in-person): **San Francisco**,
  **Santa Clara** — corrects the "Santa Clara may be free" lead; confirmed false.
- **Sacramento** portal *claims* free civil images (post-2007) — unverified.
- Verdict/valuation: **Jury Verdict Alert** covers N+S CA incl. arbitration
  awards; **JVRA does NOT cover CA** (national row in the table below is
  non-CA). CA judicial-arbitration awards (CCP § 1141.x) land in the ordinary
  public case file, subject to the same per-county fees.

### 3. Synthetic data generation (unlimited, $0, PHI-free)

```sh
curl -L -o synthea-with-dependencies.jar \
  https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar
java -jar synthea-with-dependencies.jar -p 50 -m injuries.json Massachusetts \
  --exporter.csv.export=true
grep -li "whiplash\|concussion\|spinal" output/fhir/*.json   # keep the MVA-flavored ones
```
(Java 17+. `injuries.json` module includes whiplash — "common injury in
automobile accidents" — plus fracture/concussion/laceration; also `mTBI.json`.)

Large reference files not committed (size): AZ crash-report 2023 manual (49MB)
`apps.azdot.gov/files/traffic/arizona-crash-forms-instruction-manual.pdf`;
CMS SynPUF Outpatient (33MB) + Carrier line-item claims (108MB→1.2GB CSV)
`cms.gov/data-research/statistics-trends-and-reports/basic-stand-alone-medicare-claims-public-use-files/de-10-synthetic-public-use-files-synpufs`;
Synthea prebuilt bundles `synthetichealth.github.io/synthea-sample-data/downloads/`.

## Paid sources — cheapest first (for a ~20-real-case set)

| # | Source | What you get | Price | Est. for 20 cases |
|---|---|---|---|---|
| 1 | **PACER** (pacer.uscourts.gov) | The paywalled exhibits RECAP search already identified (buying also donates them to RECAP for everyone) | $0.10/page, $3/doc cap; **fees waived if ≤$30/quarter** | **$0–60 — likely $0** |
| 2 | **UniCourt** Personal (unicourt.com/pricing) | Cross-court search incl. state; PACER pass-through at cost | $49/mo + pass-through | ~$50–110 |
| 3 | **Docket Alarm** pay-as-you-go (docketalarm.com/pricing) | Same category (Fastcase/vLex family) | $39.99/mo + per-doc | ~$100 |
| 4 | **Trellis** Personal (trellis.law/plans) | State trial-court search — covers all 15 AZ counties; 240 doc views/yr | $69.95/mo | ~$70–140 |
| 5 | **Trial Guides** books (trialguides.com) | Curated real demand-package examples w/ commentary (craft gold standard, not volume) | ~$185/book | $185–400 |
| 6 | **Arizona eAccess** (azcourts.gov/eaccess) | Official remote AZ Superior Court documents — the only remote Maricopa source | $10/doc or $80+/mo | ~$300–1,000 |
| 7 | **AAJ membership + Litigation Packets** (justice.org) | Plaintiff-bar curated exemplar demand materials | Tiered dues + per-packet (login-gated pricing) | several hundred, opaque |
| 8 | **Lexis Verdict & Settlement Analyzer** | Outcome/valuation reports (answers "what did it settle for", not "show me the letter") | $12.50/link, $125/report | $250–2,500 |
| 9 | **Westlaw** Litigation Analytics / Case Evaluator | Same category, bundled | $107–257+/mo w/ sub | $1,300+/yr |
| 10 | **JVRA / VerdictSearch** | Verdict/settlement reports | no public pricing | unknown |

Not viable: Colossus insurer-side materials (not a case-file source);
CLE "real package" handouts (attendee-gated); no open academic corpus of
demand letters exists.

## Recommended path

1. **$0 now:** mine the RECAP removal-exhibit vein harder (10–15 more letters
   realistically free) + Broward/Miami-Dade complaints for damages narratives;
   PACER's fee waiver likely covers the paywalled stragglers.
2. **~$50–100 once (recommended):** one month of UniCourt *or* Docket Alarm to
   round out 20+ full case files (letter + specials + outcome), then cancel.
3. **AZ-specific premium (~$100–300):** a targeted eAccess pull of Maricopa
   bad-faith/UIM filings — worth it right before pilot, since AZ letters
   reflect the local practice our attorneys will judge us against.
4. **Craft calibration (optional, $185):** one Trial Guides title as the
   "what excellent looks like" reference for the legal cofounder's S2 grading
   rubric.
