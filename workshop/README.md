# Workshop — owned-synthetic demo material

This tree holds the **operator-led workshop demo** inputs. Everything here is **owned-synthetic**:
authored fresh for ClarionPI, entirely fictional, and safe to display, upload, and narrate in a
live demo.

## Provenance rule (read before adding anything)

- **All content is newly fictional.** Names, providers, addresses, claim/report numbers, dates, and
  dollar figures are invented for the demo. Any resemblance to a real person, provider, or case is
  coincidental.
- **No real records, no PHI.** Nothing here is derived from a real medical record, bill, police
  report, or client file.
- **Nothing is copied from `samples/`.** That folder holds real public court records for calibration
  and is governed by [`samples/README.md`](../samples/README.md) **THE RULE** (never a fixture or
  scenario source). Workshop content is authored independently of it — not paraphrased, not seeded
  from it.
- **Deterministic + reproducible.** Generated artifacts (PDFs) are byte-reproducible from the
  checked-in source; the generators use no wall-clock or randomness. Generated PDFs are **not**
  committed — regenerate them from source (see each scenario's README).

The checked-in, reviewable deliverable is the **source text** and the **generator**, not opaque
binaries — so the owned-synthetic property is verifiable by reading, and enforced by
`backend/tests/workshop/`.

## Contents

- [`scenarios/az_mva_01/`](scenarios/az_mva_01/) — one Arizona private-party motor-vehicle-accident
  case file (police report, medical records, itemized bills, plus a duplicate) that ingests through
  the normal upload → phase0 path. This is the demo's input corpus. See its
  [`README.md`](scenarios/az_mva_01/README.md) for the upload order and the scenario "truth".

## Relationship to the release track

This is the **thin, demo-track** authoring of a synthetic scenario (roadmap slice WD-3). The
release-track slice **S12** (`backlog/workshop_mvp_plan_set/workshop_mvp_plan_set_s12_synthetic_scenario.md`)
adds the sealed-generation machinery (manifest/hash seal, immutable generation pointer, validators,
version identity). None of that machinery lives here — this tree is content + a plain generator only.
