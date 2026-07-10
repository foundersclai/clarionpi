# Samples — Reference & Calibration Material

Captured 2026-07-10 from free public sources (research provenance below). This
folder exists to answer three questions with real documents in hand:

1. **What does a real demand letter look like?** → `demand_letters/`
2. **What layouts arrive in a PI file?** (what ingest/extraction must parse) → `forms/`
3. **What PHI-safe data can seed realistic test corpora?** → `case_data/`

## THE RULE (read before using anything here)

**Nothing in this folder may be used directly as a test fixture.** Fixtures and
eval gold files stay fully synthetic (FC-v1 generator in `spikes/`, Synthea).
Court-record documents contain real names of real people — they are lawful
public records, but our fixture invariant is synthetic-only. Use this material
to: study layouts when writing extractors, feed the S1 OCR bake-off, calibrate
Brain-2 letter quality (S2/draft grading), and inform synthetic-fixture
realism. When a document below inspires a fixture, **rewrite it synthetic**.

License tiers per file (marked in the manifests):
- **[PUB]** public record / public domain (court exhibits, government forms) — unrestricted.
- **[OSS]** open-licensed synthetic data (Synthea Apache-2.0, CMS SynPUF no-DUA) — unrestricted.
- **[REF]** copyrighted or unclear-reuse material captured from freely published
  pages — internal reference ONLY; do not redistribute, do not ship in any
  artifact; the canonical copy is the source URL.

---

## demand_letters/court_records/ — 11 REAL letters [PUB]

Real demand letters filed as federal-court exhibits (mostly attached to
Notices of Removal to prove amount-in-controversy — a repeatable vein; see
[sources_and_costs.md](sources_and_costs.md)). All fetched from
`storage.courtlistener.com` (RECAP archive, free).

| File | Case | What it is |
|---|---|---|
| `wichert_v_ohio_security_UM_demand_letter.pdf` | *Wichert v. Ohio Security Ins.* (W.D. Okla. 5:21-cv-00976, doc 32-6) | **UM/MVA demand, 4pp — closest to our exact use case.** Image scan (no text layer → OCR test material) |
| `henze_v_mohave_transport_demand_letter_1.pdf` / `_2.pdf` | *Henze v. Mohave Transportation Ins.* (C.D. Ill. 2:18-cv-02107, docs 1-3/1-4) | Two MVA/trucking demand letters, image scans |
| `gutierrez_v_performance_transport_demand_letter.pdf` | *Gutierrez v. Performance Transportation* (M.D. Fla. 2:19-cv-00916, doc 1-3) | Trucking-crash demand; scanned w/ noisy OCR text layer (adversarial extraction sample) |
| `hernandez_v_thomas_presuit_demand_letter.pdf` | *Hernandez v. Thomas* (S.D. Ga. 2:22-cv-00066, doc 1-3) | 7pp pre-suit demand package w/ "TIME SENSITIVE SETTLEMENT OFFER" framing |
| `gonzalez_v_target_settlement_demand_letter.pdf` | *Gonzalez v. Target* (S.D. Fla. 1:20-cv-22765, doc 1-3) | Premises-liability settlement demand |
| `gand_removal_a_demand_letter.pdf` | *Drury v. Kroger* (N.D. Ga. 1:23-mi-99999 doc 1803-4, from Cobb Cty 23-A-1728) | 12pp premises demand package |
| `gand_removal_b_demand_letter.pdf` | N.D. Ga. 1:23-mi-99999 doc 2210-3 | 18pp "Confidential Settlement Communication" to Landstar's claims administrator (trucking) |
| `gand_removal_c_demand_letter.pdf` | N.D. Ga. 1:23-mi-99999 doc 1880-2 | 3pp short-form demand, image scan |
| `scott_v_travelers_statutory_60day_demand.pdf` | *Scott v. Travelers* (N.D. Ga. 1:20-cv-04420, doc 28-3) | Georgia statutory 60-day insurer demand, image scan |
| `andrews_v_autoliv_demand_letter.pdf` | *Andrews v. Autoliv Japan* (N.D. Ga. 1:14-cv-03432, doc 536-1) | 9pp Butler Wooten demand in an airbag-defect death case (top-tier plaintiff-firm craft) |

URL pattern for all: `https://storage.courtlistener.com/recap/gov.uscourts.<court>.<pacer_case_id>/gov.uscourts.<court>.<pacer_case_id>.<doc>.<attachment>.pdf`

Note: 4 of 11 are image-only scans — deliberately kept; they are exactly what
the S1 OCR bake-off and the `zero_text` ingest path need.

## demand_letters/published_samples/ — 12 published specimens [REF]

Captured from freely published law-firm/CLE pages; **internal reference only —
link, don't copy, in anything we produce.**

- `az_lambergoodnow_*.md` — four **Arizona-specific** fill-in templates from a
  Phoenix firm (liability carrier, UIM follow-on, motorcycle, wrongful death).
  The liability-carrier one is the best structural match for our letter: it
  even instructs including ICD/CPT codes "because insurers use computer
  evaluation software" — exactly the Colossus-aware drafting our attorneys
  expect. Source: lambergoodnow.com/personal-injury-counsel-center/.
- `mz_*.md` — four Miller & Zois (MD) letters derived from real filed cases
  (case numbers in text; one settled $750K; one is a pure policy-limits
  bad-faith setup citing *State Farm v. White* factors).
- `coplancrane_wsba_demand_package.pdf` — 20pp WSBA CLE "The Demand Package"
  presentation w/ attachments.
- `justinziegler_sample_letter_geico_BI_insurer.pdf` — real initial
  notice-of-representation letter to GEICO (the letter that *precedes* the
  demand — a distinct document class ingest will meet).
- `brennerbondurant_howto_template.pdf`, `injuryag_auto_demand_package_guide.md`
  — generic template + package-assembly guide.

**Observed skeleton across all real + published letters** (validates our
`az.yaml letter_structure`): re-line w/ claim # + "FOR SETTLEMENT PURPOSES
ONLY" → liability narrative w/ statute cite → chronological treatment tied to
providers → itemized specials table w/ total (+ future meds, wage loss) →
general-damages narrative → specific-dollar or policy-limits demand w/
deadline + exhibit list. Bad-faith variants add insured's coverage amount and
frame the deadline as protecting the insured — a v2 letter mode to discuss
with the legal cofounder.

## forms/ — the paper a PI file is made of [PUB unless noted]

- `az_crash/` — **AZ Crash Report blank (form 01-2704, R11/17)** + the 2017
  field-code instruction manual (49MB 2023 manual: link in sources doc) + ADOT
  MVR request (46-4416) + ADOT insurance-info request (40-5901). The crash
  report is the #1 extraction target; the manual decodes its numeric field
  codes.
- `medical_records_hipaa/` — real AZ hospital-system HIPAA authorizations
  (Banner, HonorHealth 2025), an AZ authorization citing A.R.S. § 12-2294(E)
  (Ambetter), and a records-request cover-letter template [REF]. Ingest must
  distinguish the transmittal letter from the authorization it encloses.
- `billing/` — official blank **CMS-1500** + CMS filled sample claim + Medicare
  manual ch. 26 (field-by-field), **UB-04** manual ch. 25 + Mississippi
  Medicaid visual guide (filled example) + NUBC sample TOC, and CMS's sample
  **EOB** (an EOB is *not* a bill — the ledger must never double-count it).
- `liens_notice_claims/` — AHCCCS third-party-liability handout (lien priority
  ladder), **Maricopa County + City of Phoenix notice-of-claim forms**
  (A.R.S. § 12-821.01 — the 180-day trap; a claim without a specific dollar
  amount is void), AZ AG agency handbook ch. 13. **Research finding: no
  standard A.R.S. § 33-931 hospital-lien form exists** — liens arrive as
  free-text "verified statements"; ingest needs a loose pattern, not a
  template.
- `insurance/` — ACORD 2 Automobile Loss Notice (the industry first-notice-
  of-loss layout; classify as claim-intake paper).

## case_data/ — PHI-safe seeds for realistic corpora

- `synthetic_patients/sample_patient_fhir_bundle.json` [OSS] — one Synthea
  patient: 187 FHIR resources incl. 17 Claims + 13 EOBs w/ dollar amounts.
  Generation recipe for MVA-flavored patients (whiplash/concussion modules) is
  in [sources_and_costs.md](sources_and_costs.md).
- `cms_synpuf/` [OSS] — CMS DE-SynPUF synthetic Medicare claims: Inpatient
  sample zip (institutional claims), Beneficiary summary zip, codebook + user
  guide + FAQ. Realistic ICD/HCPCS/charge distributions for bill synthesis.
  (33MB Outpatient + 108MB Carrier line-item files: URLs in sources doc.)
- `crash_reports/` — MMUCC 6th ed. (the federal crash-report data-element
  standard) [PUB], **two filled-in crash reports with officer narratives**
  (ReportBeam FL + OH vendor demos) [REF], PennDOT crash-report manual [PUB],
  Chicago SR-1050 blank [PUB].
- `medical_notes/` [REF — "reference purpose only" per source] — six MTSamples
  teaching notes incl. **a genuine rear-end MVA ER note** (32F,
  seatbelt+airbag, neck/abdo pain — `er_1_motor_vehicle_accident.txt`), a
  motorcycle-crash ankle ORIF op note, a PT note; plus an EM-residency
  billing/coding packet.
- `bills_superbills/` — real-but-redacted itemized hospital bills (CALMBA
  [REF], Virginia Victims Fund [PUB]), a fully synthetic annotated hospital
  bill (SSM, fake patient) [REF], filled + blank superbills, NUCC CMS-1500
  instruction manual v11 [PUB].

**Recipe for one coherent fake MVA case file** (the pilot-shaped corpus):
Synthea patient spine (identity/dates/$) + `er_1` ER-note structure + PT/ortho
note templates rewritten to the crash + bills rendered on the CMS-1500/UB-04
layouts with SynPUF-realistic codes/amounts + an original police-report
narrative laid out per MMUCC fields. Every word synthetic, every layout real.
