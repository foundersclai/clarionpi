# Scenario: az_mva_01 — Arizona private-party motor-vehicle accident

Owned-synthetic demo case file (see [`../../README.md`](../../README.md) for the provenance rule).
Everything below is fictional and authored for the demo.

## Generate the upload PDFs

```
python workshop/scenarios/az_mva_01/generate.py
```

Writes eight text-layer PDFs to `workshop/scenarios/az_mva_01/pdf/` (git-ignored — regenerate any
time; output is byte-identical). Upload them through the normal workbench upload flow.

## Upload order and expected handling

| # | File | Expected classification | Notes |
|---|---|---|---|
| 1 | `01_police_report.pdf` | `police_report` | carries the date of loss + at-fault finding |
| 2 | `02_er_note.pdf` | `medical_record` | ER encounter, diagnoses |
| 3 | `03_er_bill.pdf` | `bill` | itemized ER charges |
| 4 | `04_ortho_notes.pdf` | `medical_record` | four orthopedic visits |
| 5 | `05_ortho_bill.pdf` | `bill` | itemized orthopedic charges |
| 6 | `06_pt_notes.pdf` | `medical_record` | physical-therapy course |
| 7 | `07_pt_bill.pdf` | `bill` | itemized PT charges |
| 8 | `08_er_bill_resend.pdf` | duplicate of #3 | byte-identical re-send → **dedup / review queue** beat |

**Classification depends on `LLM_PROVIDER`.** With a live provider (`anthropic`) the first-page text
sample auto-types each document. With the **default** `LLM_PROVIDER=null`, classification degrades
to the **review queue** by design (every doc lands as `other` + `needs_review`) — which is itself a
demo beat: the attorney reclassifies from the queue. Either way the documents carry a real text
layer, so ingest never needs OCR.

## Scenario "truth" (for narration / expected extraction)

- **Parties (private individuals):** plaintiff **Marisol Rivas**; at-fault driver **Kenneth Doyle**.
  Two private motorists — **no** government or public-entity vehicle.
- **Carrier / claim:** Apex Mutual Insurance Company, claim `APX-2025-0416632`.
- **Date of loss:** **March 14, 2025**, Phoenix (Maricopa County), Arizona.
- **Deadline applicability (ties to WD-1):** private-party MVA → the **2-year** Arizona statute of
  limitations (A.R.S. § 12-542) applies → **March 14, 2027**. Because no public entity is involved,
  the 180-day public-entity notice-of-claim deadline is **not applicable** and should be suppressed.
- **Injuries:** cervical strain, lumbar strain, right-shoulder partial supraspinatus tear.
- **Providers:** Saguaro Regional Medical Center (ER); Desert Sky Orthopedics; Cactus Valley
  Physical Therapy.
- **Billed charges (authored literals — no computed currency):**
  - ER: **$18,750.00**
  - Orthopedics: **$6,400.00**
  - Physical therapy: **$3,900.00**
  - **Grand billed: $29,050.00** (the demand's billed-damages anchor)

All figures are fixed synthetic values printed on the documents; the generator performs no
arithmetic. If you change any figure, update the matching line items so the stated totals still add
up, and re-check `backend/tests/workshop/test_az_mva_scenario.py`.
