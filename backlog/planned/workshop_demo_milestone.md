# WD-0 — Operator-led attorney demo milestone (roadmap umbrella, non-release)

- Status: ROADMAP UMBRELLA — a non-release milestone. Implements nothing directly; each slice
  below is its own repository plan with its own tier assessment, consensus, and attestation. This
  file carries no SDLC tier block for that reason (a roadmap declares no code change to tier).
- Relationship to the workshop plan set: WD-0 sits between S1 (complete) and S2. S19 remains the
  sole Workshop Release gate (`backlog/workshop_mvp_plan_set/umbrella.md:100-107` unchanged). WD-0
  claims no release, no send-readiness, and no legal approval; it exists to get attorney feedback
  and open the paid-review funnel months before the hardened release.
- Derivation: the slice ledger below is derived from the exact demo script, not a slice-count
  target. Each slice names its owning plan-set slice (S9/S12/S17/S18) and whether it is the full
  slice or a thin sub-scope; every thin sub-scope is a judgment call resolved in that slice's own
  plan, not here.
- Track status (2026-07-19): **all four slices merged to `main`** — WD-1 (#2), WD-2 (#3), WD-3 (#4),
  WD-4 (#5). The only milestone step remaining is the acceptance rehearsal below. This file is
  committed as the durable record; the original working copy was never committed and was
  reconstructed from the session transcript.

## Objective

One 20-minute, operator-led, synthetic-only demo of the shared product path (upload → gates →
draft → compliance → package → provenance click-through) to a prospective Arizona PI attorney, on a
disposable local environment, twice rehearsed, with a feedback form and paid-review ask at the end.

## Non-release guardrails (inherited, not new)

- Owned-synthetic sources only per `workshop/README.md`; `samples/` remains forbidden as fixture or
  scenario source (THE RULE).
- Operator laptop, `APP_ENV=dev`, disposable database; attendees never touch the system; no
  attendee uploads; no hosting. S11 runtime profile, S13 purpose persistence, S14 replay, and S15
  sealed lifecycle are therefore not required for WD-0 (they guard exactly the surfaces WD-0 does
  not expose).
- Verbal plus slide disclosure that all output is simulated and not legal work product; no
  attestation copy is drafted (SRC-20 held; ADR-0009 stays reserved).
- Workshop/demo evidence closes no legal, PHI, ethics, BAA, or live-pilot gate (SRC-22); WD-0
  acceptance feeds only the roadmap and the recruitment funnel.
- Gate meanings unchanged: the operator clicking G3 approval on a synthetic matter is presented as
  simulation, never as attorney approval.

## The 20-minute script (requirements source)

Beats 3–5 are pre-staged: the operator runs ingest/extraction/analysis with a live
`LLM_PROVIDER=anthropic` the day before and the demo navigates the prepared matter. Beats 7–8 run
live (no model call is needed to build the package). S14 canonical replay is explicitly not a WD-0
dependency; a network outage during pre-staging delays rehearsal, not the meeting.

| # | Min | Beat | Surface |
|---|---|---|---|
| 1 | 0–2 | Framing + synthetic-only disclosure slide | kit |
| 2 | 2–4 | Login, matter dashboard, open the demo matter | `frontend/app/login`, `recent-matters-list` |
| 3 | 4–7 | Corpus: uploaded docs, classification, dedup/review queue | `documents-panel` |
| 4 | 7–9 | G1 intake + deadline banner (correct for a private-party defendant) + G1.5 | `intake-flags`, `deadline-banner`, `gate-stepper` |
| 5 | 9–12 | Evidence workbench: chronology, risk flags, a source-row edit, PDF page anchor | `evidence-workbench`, `pdf-page-view` |
| 6 | 12–15 | Strategy intake → plan review → demand generation with tokens | `strategy-intake-card`, `plan-review-card`, `demand-generation-card` |
| 7 | 15–17 | Compliance findings + G3 approval | `compliance-panel` |
| 8 | 17–19 | Package build: letter.docx, binder.pdf with Bates, provenance report; token → source-page click-through | `package-card`, `provenance-viewer` |
| 9 | 19–20 | Feedback form + paid-review ask | kit |

Scripting note (beat 6): the requested-demand election is persisted but unconsumed downstream
(`backend/app/engine/brain2/plan.py:372`). The script features the computed demand and does not
demo the election. This is a scripting choice, not a slice; pulling a thin S7 forward is out of
WD-0 scope.

## Demo-track slice ledger

Each row is a separate, independently mergeable repository plan. Mechanism, file:line grounding, and
the judgment calls live in each slice's own plan — this ledger only points at them. "Natural tier"
is each slice's own assessment to confirm; the two code slices carry the Tier-3 triggers identified
for this track.

| Slice | Delivers | Owning slice | Natural tier | Mergeable now | Status |
|---|---|---|---|---|---|
| WD-1 | Suppress the inapplicable public-entity notice-of-claim deadline for private-party intake | S9 (full) | 3 | yes | merged — PR #2 (`789843d`) |
| WD-2 | Wire G3 approve → draft `APPROVED` so `buildable` and the FE build hint tell the truth (the package already builds) | S18 (thin) | 2 (approved) | yes | merged — PR #3 (`5e45784`) |
| WD-3 | One owned-synthetic Arizona scenario (police report, bills, records), ingested via normal upload | S12 (thin) | 1 (data) | yes | merged — PR #4 (`ed39c6c`) |
| WD-4 | Demo kit: talk track, disclosure slide, feedback form, paid-review one-pager | S17 (thin) | 1 (materials) | yes | merged — PR #5 (`29a7e81`) |

Seed pointers and per-slice cautions (for the slice authors, not commitments):

- WD-1 — `backend/app/rules/deadlines.py::_rule_applies` treats a `claim_type is None` rule (the
  public-entity notice-of-claim trap) as applying to every matter; thread intake context via
  `backend/app/api/routes/matters.py`. Demo-independent product-correctness fix; valuable on its own.
- WD-2 — verify-first **done** (2026-07-18): the package **already builds** end-to-end — the build
  route `post_package_build` fences only on `gate_state == PACKAGE_ASSEMBLY` (drafting.py:613), not
  on draft status, and `test_m5_exit_full_demand_package` proves it. The real defect is narrower:
  `DraftStatus.APPROVED` is **never assigned** — the `(COMPLIANCE_REVIEW, G3_APPROVED)` transition
  (machine.py:78) runs no side effect (`_SIDE_EFFECTS`, service.py:486, has no G3 entry), so the
  draft stays `IN_COMPLIANCE`, `buildable` (view_models.py:681) is permanently False, and the FE
  package-card permanently shows a misleading "the draft is not approved yet — building will refuse"
  hint (package-card.tsx:223) on a matter whose build works. Fix: add
  `(COMPLIANCE_REVIEW, G3_APPROVED) → _approve_draft` setting `latest_draft(...).status = APPROVED`
  (completes the intended `DraftStatus` design). Cascade-safe: a later registry bump still supersedes
  the approved draft via `registry_bump.py:90`. The earlier "package unreachable / Tier 3" framing
  was overturned by this check.
- WD-3 — owned-synthetic only per `workshop/README.md`; `samples/` forbidden as source. Content
  authoring, no manifest/loader/seal machinery (that is release-track S12).
- WD-4 — substitutes slide/verbal disclosure for release-grade S13 product-enforced labeling
  because attendees never operate the product; the slice plan confirms that substitution is
  acceptable for a non-release demo.

## Explicitly deferred to the release track (why safe for WD-0)

- S2–S8 durable ownership, generations, ordering, anchored tokens, settlement, provenance closure:
  a single operator-driven happy path on a fresh disposable database does not hit the concurrency,
  invalidation, and identity defects these slices harden against (except the WD-2 carve-out above).
- S10 chronology byte-determinism: no one diffs artifact bytes in a live demo.
- S11/S13/S14/S15 profile, purpose persistence, replay, sealed lifecycle: guard surfaces WD-0 does
  not expose (attendee interaction, offline replay, multi-session recovery).
- S16 fenced publication and S19 acceptance: release-gate machinery; WD-0 is not a release.

## Acceptance (milestone, not release)

Runs only after WD-1–WD-4 land, on a fresh disposable database with the owned-synthetic scenario:

- Two complete end-to-end UI rehearsal passes of the full script. Frontend components exist for
  every scripted beat (`evidence-workbench`, `plan-review-card`, `compliance-panel`, `package-card`,
  `provenance-viewer`) though the repo map documents FE scope only through G1.5, so rehearsal is
  where UI gaps surface. Fix-forward small UI defects; any structural gap becomes a new named slice,
  not a fix-in-place.
- All four artifacts open; provenance click-through resolves a demanded amount to its source page;
  no public-entity deadline appears for the private-party matter.
- Disclosure slide present in the kit; feedback form and paid-review one-pager ready.
- `make verify` green; no new runtime capability reachable outside `APP_ENV=dev`; no edit to the
  attested S1 plan file.

## Process

1. Standing prerequisite (from the plan set): publish local `main` to the authenticated but empty
   `origin` before any child implementation branch; preserve the untracked `scripts/claude-plan-review`.
2. Per code slice (WD-1, then WD-2): draft its repository plan → `sdlc-tier-assessment` →
   `plan-consensus-loop` → `implement-pr-loop`. WD-2 runs its verify-first scope check before the
   plan claims thin scope.
3. WD-3 and WD-4: lightweight plans (likely Tier 1); WD-3 under the `workshop/README.md`
   synthetic-source governance.
4. Amend `backlog/workshop_mvp_plan_set/umbrella.md` to reference WD-0 as an explicitly non-release
   milestone between S1 and S2, leaving S19 as the only release gate.
5. After WD-1–WD-4 land, run the milestone acceptance above.
