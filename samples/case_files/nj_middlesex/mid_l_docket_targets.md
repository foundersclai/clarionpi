# MID-L Docket Targets — Phase-2 Work Queue (eCourts jacket pulls)

Harvested 2026-07-10 from App Div opinions (njcourts.gov) + CourtListener RECAP
removal notices. Status: `captured` = document already in this folder;
`recap` = removal packet fetchable free (use host `storage.courtlistener.com`,
NOT `www.courtlistener.com/recap/` which 403s); `ecourts-only` = never removed
to federal court, so documents exist ONLY in the NJ eCourts case jacket
(requires the free registered login) — these are the phase-2 payoff, incl.
arbitration awards (NJ mandatory arb for auto).

| Docket | Case | Type | Status |
|---|---|---|---|
| MID-L-2505-25 | Tenenbaum v. Allstate | UM/UIM bad-faith (IFCA) | ecourts-only ([published App Div 4/29/26](https://www.njcourts.gov/system/files/court-opinions/2026/a0742-25a0988-25.pdf) — sever/stay of bad-faith discovery) |
| MID-L-2601-25 | Cirelli v. GEICO | UM/UIM bad-faith (IFCA) | ecourts-only (companion to Tenenbaum) |
| MID-L-6378-24 | Senape v. South Amboy HS | Premises, public entity | ecourts-only ([A-1329-24](https://www.njcourts.gov/system/files/court-opinions/2025/a1329-24.pdf) — late TCA notice) |
| MID-L-3267-24 | Giannettino v. iPlay America | Premises, amusement | ecourts-only ([A-0850-24](https://www.njcourts.gov/system/files/court-opinions/2025/a0850-24.pdf)) |
| MID-L-466-24 | Lee v. Cesse (Hub Group) | Trucking | recap (D.N.J. 3:24-cv-04509) |
| MID-L-3665-23 | Supreme v. Village Super Market (ShopRite) | Premises slip/fall | ecourts-only ([A-2305-24](https://www.njcourts.gov/system/files/court-opinions/2026/a2305-24.pdf) — mode-of-operation) |
| MID-L-208-23 | McMillian v. GEICO Indemnity | UM/UIM | recap (D.N.J. 1:23-cv-01671) |
| MID-L-2754-22 | Miles v. Allstate | UM/UIM | recap (njd.498879; remanded 8/2022 → jacket continues in eCourts) |
| MID-L-1442-22 | Chun-Lee v. Toczylowski (L&M Express) | Trucking | **captured** (notice of removal) |
| MID-L-1044-22 | Erwin v. Brown | MVA | recap (D.N.J. 2:22-cv-01794) |
| MID-L-140-22 | Brites v. BJ's Wholesale | Premises | recap (D.N.J. 2:22-cv-00279) |
| MID-L-5362-21 | Beny-Arid v. O'Brien (Indian River Transport) | Trucking | recap; remanded by consent → eCourts jacket has the rest |
| MID-L-004864-21 | Byrd v. Raikes | MVA | **captured** (47pp complaint package w/ Title 39 cert) |
| MID-L-005904-21 | Jay v. Vance | MVA | **captured** (complaint) |
| MID-L-006674-21 | Leporino v. Home Depot | Premises | **captured** (complaint) |
| MID-L-003198-21 | Caffrey v. McArthur Express | Trucking | **captured** (complaint) |
| MID-L-4927-21 | DiCostanzo v. Target | Premises | recap (56pp state file; skipped — no letter) |
| MID-L-5735-20 | Cintron v. J.B. Hunt | Trucking | **captured** (complaint + removal w/ $5M demand recital) |
| MID-L-4607-20 | Widofsky v. New Brunswick Parking Auth. | Premises, public entity | ecourts-only ([A-3837-21](https://www.njcourts.gov/system/files/court-opinions/2024/a3837-21.pdf) — unmarked glass wall) |
| MID-L-2383-20 | Quinones v. Costco | Premises | recap (D.N.J. 3:20-cv-05798) |
| MID-L-8494-19 | Barclay v. 7-Eleven | Premises | recap (exhibit downloaded to staging, not committed) |
| MID-L-5753-19 | Paden v. Coddington (Blackhawk Transport) | Trucking | recap (staging, not committed) |
| MID-L-5518-19 | Thomas v. BJ's Wholesale | Premises | recap (D.N.J. 3:19-cv-17048) |
| MID-L-1647-19 | Bellone v. Stone (Ward Trucking) | Trucking | recap (njd.402841.1.1) |
| MID-L-830-19 | Perez v. Canapino (USA Truck) | Trucking | recap (staging, not committed) |
| MID-L-001382-19 | Henderson v. Crown Trucking | Trucking/MVA | **captured** (lifecycle packet w/ demand letter) |
| MID-L-8396-18 | Elsayed-Shahin v. Muniz-Jusino | MVA | recap (D.N.J. 2:19-cv-00463) |
| MID-L-3003-18 | Spinella v. Walmart | Premises | recap (staging, not committed) |
| MID-L-4897-18 | Palombo v. Residence Inn | Premises, hotel | recap (D.N.J. 3:18-cv-13724) |
| MID-L-4991-18 | Doctors v. NJM | UM/UIM | ecourts-only ([A-2898-18T3](https://www.njcourts.gov/system/files/court-opinions/2020/a2898-18.pdf) — time-barred) |
| MID-L-2106-17 | Falk v. Donovan / USAA | UM/UIM | ecourts-only ([A-4236-18T4](https://www.njcourts.gov/system/files/court-opinions/2020/a4236-18.pdf) — $500K UIM award reversed) |

Excluded during verification: Burden v. Mid-Century (Essex), Vanrell v. USAA
(Atlantic), pre-2017 dockets (no e-filed PDFs), and MID-L-numbered mass-tort /
TCPA / PFAS cases (off-topic).

## Phase-2 recipe (once logged into eCourts)

1. njcourts.gov → Find a Case → Civil and Foreclosure Public Access → login.
2. Search by docket number (venue Middlesex, docket type L). Open the case
   jacket; every e-filed document lists with its blue-stamp receipt.
3. Priority pulls, in order: **arbitration awards** (R. 4:21A — injury +
   award amounts, the valuation dataset), **Title 39 physician certifications**,
   complaints/answers for the `ecourts-only` rows above, any motion exhibits in
   the UM/UIM rows (that's where letters hide).
4. Manual, targeted pulls only — no bulk scraping (Judiciary AUP).
