# Talk track — 20-minute operator-led demo

Audience: personal-injury attorneys. The operator drives; attendees watch. Synthetic scenario only
(*Rivas v. Doyle*, from `../scenarios/az_mva_01/`). **Read `disclosure.md` first.**

Legend — **[PRE-RUN]**: executed before the meeting (the model work ran ahead of time; you navigate
the saved results). **[LIVE]**: done live in the room.

## Before the room
- Fresh disposable database; owned-synthetic scenario only; LLM key configured.
- `python workshop/scenarios/az_mva_01/generate.py`; have the eight PDFs ready to upload.
- Pre-run the model-heavy beats so the analysis, draft, and package already exist to navigate.
- Save screenshots / a recording of the pre-run beats as a fallback.

## Beats

1. **[LIVE] Frame + disclosure (2 min).** Show the disclosure slide. Say it plainly: a demonstration,
   synthetic data, not legal advice, and no lawyer has reviewed today's output.
2. **[LIVE] Login (0.5 min).** Log in as the demo attorney. Note this is one shared product path —
   not demo-only theater.
3. **[LIVE] Create the matter (1 min).** New matter; show the pilot-eligibility box. This is
   *Rivas v. Doyle*, a synthetic Arizona motor-vehicle case — no real client.
4. **[PRE-RUN] Upload the corpus (1.5 min).** Eight documents: a police report, ER/ortho/PT records,
   itemized bills, and a re-sent bill. Show that ingest read every page's text layer.
5. **[PRE-RUN] Classification + dedup (1.5 min).** Each document is typed (police report / bill /
   record); the re-sent ER bill is flagged as a duplicate in the review queue. The attorney works
   that queue — the software never guesses silently.
6. **[LIVE] G1 — facts + deadlines (2 min).** Confirm the facts. Show the deadline: this is a
   **private-party** collision, so the **2-year Arizona statute (through 2027-03-14)** applies and
   the 180-day public-entity notice-of-claim deadline is **correctly absent**. This is the moment
   that proves the software knows *which* deadline applies.
7. **[LIVE] G1.5 — strategy intake (1 min).** Advance the strategy gate.
8. **[PRE-RUN] Evidence + analysis (1.5 min).** Walk the chronology and the risk read assembled from
   the records.
9. **[LIVE] G2.5 — plan approve (1 min).** The attorney approves the strategy plan; note the plan is
   pinned to the current facts.
10. **[PRE-RUN] Drafting (2 min).** Walk the generated demand letter. It is a machine draft — **not
    attorney-reviewed** work product. Point out the billed-damages anchor: **$29,050.00**.
11. **[LIVE] G3 — compliance + approve (1.5 min).** Run compliance; the attorney approves. The
    package becomes buildable only after this approval.
12. **[LIVE] Build the package (1 min).** Build the letter, exhibit binder, chronology, and
    provenance report.
13. **[LIVE] Downloads + provenance click-through (2 min).** Open the binder. Click the **$29,050.00**
    figure and land on the exact source page it came from; repeat for one more amount. Page-level
    provenance is the whole point — every number traces to a page.
14. **[LIVE] Close (1 min).** Recap: which deadline applied, where the numbers came from, and what a
    lawyer still has to do. Hand out the feedback form and the paid-review one-pager.

## If something breaks
- Fall back to the saved screenshots / recording of the pre-run beats and keep narrating.
- Never enter a real client's information to "make it realer" — that voids the disclosure.
