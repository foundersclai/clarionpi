# Workshop MVP implementation plan set

## Source

This plan set is derived from the immutable snapshot `source_input_01.md`, copied byte-for-byte
from `backlog/planned/workshop_mvp.md` after its cross-engine consensus review. The source remains
the authority for detailed legal limitations, failure protocols, exact acceptance cases, and the
full eighteen-row adversarial matrix.

## Goal and non-goals

Deliver an offline, synthetic-only ClarionPI workshop track by extending the shared product path,
not by creating a second workflow. Every demo matter and artifact remains permanently restricted.
Live matters, PHI, legal approval, production hosting, and a claim of send-readiness remain outside
this plan set.

## Confirmed decisions and constraints

- Use `APP_ENV=dev` with a separate fail-closed workshop capability profile and local replay only.
- Preserve ordinary G1, G1.5, G2a, G2.5, and G3 actions; simulation never becomes legal approval.
- Land diagnostics before fixes for every silent wrong-output credibility defect.
- Keep all currency arithmetic in `backend/app/money` and all model calls behind `backend/app/core`.
- Persist immutable demo identity, durable operation ownership, exact approval bindings, and
  collision-free artifact publication before workshop content can be served.
- Treat the scenario, replay catalog, lifecycle workspace, and render toolchain as sealed inputs.
- Keep ADR-0009/WI-1 and WI-3/WI-4 held; do not draft legal attestation wording.

## Live-code evidence

- `backend/app/core/llm_provider.py:182-196` reads `LLM_PROVIDER` directly and has no replay mode.
- `backend/app/engine/brain2/plan.py:372` persists the requested amount, while current token mint,
  allocation, rendering, and package paths do not consume that plan field.
- `backend/app/api/routes/matters.py:93` calls deadline computation without WI-2 intake context.
- `backend/app/engine/tokenizer/registry.py:465-505` mints ledger amounts without derived anchors.
- `backend/app/corpus/ingest/dedup.py:115-117` falls back from timestamp ties to UUID identity.
- `backend/app/engine/orchestrator/service.py:486-489` has no G3 draft-approval side effect, while
  `backend/app/api/view_models.py:681` requires an approved draft for a buildable package.
- `CONTRACTS.md` assigns the affected corpus, core, orchestrator, brain1/brain2, tokenizer, rules,
  money, package, and API boundaries to their current module-contract documents.

## Source ledger

| Source ID | Confirmed item | Disposition | Live owner/evidence | Slice |
|---|---|---|---|---|
| SRC-01 | Freeze the workshop boundary, ADR sequence, contracts, and tenant-key rules | implement | `docs/adr/`; `CONTRACTS.md`; source §7 WMVP-00 | S1 |
| SRC-02 | Establish durable operation ownership and atomic result settlement | implement | `backend/app/core`; source WMVP-01G-a/BM-13 | S2 |
| SRC-03 | Own upload ordinals, corpus heads, and deterministic duplicate selection | implement | `backend/app/corpus/ingest`; source WMVP-01D/BM-06 | S3 |
| SRC-04 | Make derived rows, prompts, tokens, risks, and package inputs semantically ordered | implement | `backend/app/engine`; source WMVP-01E/BM-07 | S4 |
| SRC-05 | Publish immutable analysis/evidence generations and exact gate bindings | implement | `backend/app/engine/brain1`; source WMVP-01G-b | S5 |
| SRC-06 | Resolve medical amount tokens to all contributing bill-page anchors | implement | `backend/app/money`; tokenizer registry; source WMVP-01C-a | S6 |
| SRC-07 | Settle the final requested demand through immutable elections, plans, and exact G2.5 approval | implement | brain2/orchestrator/tokenizer; source WMVP-01A/BM-09 | S7 |
| SRC-08 | Close requested-demand and specials provenance through package authority | implement | tokenizer/package/provenance API; source WMVP-01C-b/BM-10-11 | S8 |
| SRC-09 | Suppress public-entity deadlines for private-party intake | implement | `backend/app/rules/deadlines.py:42`; source WMVP-01B/BM-08 | S9 |
| SRC-10 | Diagnose and close or explicitly constrain chronology byte nondeterminism | implement | package chronology builder; source WMVP-01F | S10 |
| SRC-11 | Add validated workshop runtime composition, session security, and authoritative disclosure | implement | `backend/app/core`; auth/runtime API; source WMVP-02/BM-01-02 | S11 |
| SRC-12 | Generate and seal one owned synthetic Arizona scenario and manifest | implement | new `workshop` owner; source WMVP-04/BM-04 | S12 |
| SRC-13 | Persist immutable demo purpose, enforce content policy, and own the evidence session | implement | matter/evidence ORM/API; source WMVP-03/BM-03 | S13 |
| SRC-14 | Add strict canonical replay with invocation, budget, and call provenance | implement | core provider/telemetry; source WMVP-05/BM-12-14 | S14 |
| SRC-15 | Seal uploads and own prepare/reset/checkpoint/supervisor/doctor recovery | implement | corpus ingest plus workshop lifecycle; source WMVP-06/BM-05/15 | S15 |
| SRC-16 | Publish four permanently restricted artifacts through fenced immutable publication | implement | package/storage/API; source WMVP-07/BM-16 | S16 |
| SRC-17 | Produce the workshop kit, evidence export presentation, feedback, and paid-review funnel | implement | workshop materials; source WMVP-09 | S17 |
| SRC-18 | Prove the isolated full HTTP flow, UI truth, artifacts, and rehearsal acceptance | implement | backend/frontend integration; source WMVP-08/BM-18 | S19 |
| SRC-19 | Live matters, PHI, attendee uploads, and public hosting are excluded | non-goal | source §§5.2 and 15 | — |
| SRC-20 | WI-3, WI-4, ADR-0009/WI-1, and legal attestations remain held | non-goal | source §§2, 13, and 15 | — |
| SRC-21 | Arizona legal review and relationship diligence require a paid human engagement | non-coding | source WMVP-09 recruitment funnel | — |
| SRC-22 | Workshop evidence cannot close legal, ethics, HIPAA, BAA, or live-pilot gates | non-goal | source §3 evidence boundary | — |
| SRC-23 | The pre-existing untracked Claude helper is not part of the workshop implementation | non-goal | source §2; `scripts/claude-plan-review` | — |
| SRC-24 | Seal immutable draft/finding history and exact G3 package authority | implement | brain2/compliance/orchestrator; source WMVP-05 | S18 |

## Affected boundaries

The set covers runtime composition; session/CSRF and frontend truth; persisted matter-purpose
policy; scenario generation; upload sealing; ingest and semantic ordering; deadline applicability;
immutable strategy/election/plan settlement; amount and page provenance; canonical replay;
durable operation and provider settlement; SSE terminal truth; workspace lifecycle; artifact
publication and serving; module ownership; and final isolated HTTP integration.

## Slice graph

| Slice | Dependency tier | Mergeability | Deployment | Safe intermediate state |
|---|---:|---|---|---|
| S1 charter/contracts | 0 | independent | safe | Documentation and contract registrations add no runtime capability. |
| S2 durable run base | 1 | ordered after S1 | dormant | New run records are unused until callers migrate. |
| S3 ingest identity/order | 2 | ordered after S2 | safe | Existing behavior reads the new additive identities after migration. |
| S4 semantic ordering | 3 | ordered after S3 | safe | Shared outputs become deterministic without workshop activation. |
| S5 analysis/evidence generations | 4 | ordered after S2-S4 | safe | Current pointers advance only with complete owned generations. |
| S6 amount-anchor resolution | 5 | ordered after S3-S5 | safe | New anchored tokens coexist with invalidated legacy history. |
| S7 requested-demand settlement | 6 | ordered after S5-S6 | safe | New plans require exact elections; legacy plans cannot gain authority. |
| S8 provenance closure | 7 | ordered after S6-S7 | safe | Package authority accepts only complete typed provenance. |
| S9 deadline applicability | 1 | ordered after S1 | safe | Standard private-party matters stop receiving an inapplicable candidate. |
| S10 chronology determinism | 1 | ordered after S1 | safe | Either bytes stabilize or the limitation remains explicit and tested. |
| S11 runtime profile | 1 | ordered after S1 | dormant | Workshop boot remains refused until later capability owners land. |
| S12 synthetic scenario | 1 | ordered after S1 | dormant | Generated bundles are inert data with no runtime loader. |
| S13 persisted identity/evidence | 5 | ordered after S5 and S11 | feature_gated | Content and interactive writes require matching purpose and running evidence authority. |
| S14 canonical replay | 8 | ordered after shared baseline, S11-S13 | feature_gated | Replay can run only through validated workshop composition. |
| S15 lifecycle and sealed upload | 10 | ordered after S11-S14 and S18 | feature_gated | Preparation stages immutable generations only after draft/compliance authority exists. |
| S16 restricted artifacts | 10 | ordered after shared baseline and S12-S15/S18 | feature_gated | Demo builds stay refused until publication, exact G3, and policy checks are complete. |
| S17 workshop kit | 6 | ordered after S1 and S13 | dormant | Materials cannot create or advance product state. |
| S18 draft/compliance authority | 9 | ordered after S2, S5, S7, and S14 | safe | Replay stays gated until exact draft/finding/G3 settlement exists. |
| S19 integration/rehearsal | 11 | ordered after every earlier slice | atomic_release | No workshop release exists until the complete acceptance gate passes. |

## Integration acceptance

S19 is the unique integration owner and directly depends on S1-S18. It runs the complete session
and Origin-protected upload-to-package flow three times on disposable roots, the Postgres lock tier,
the pinned offline renderer, frontend truth tests, evidence export checks, and five manual rehearsal
cycles. It also proves standard-profile behavior remains free of workshop labels and restrictions.

## Unresolved blockers

No plan-set design blocker remains. Before any child implementation branch starts, publish the
current local `main` to the authenticated but currently empty `origin`; preserve the unrelated
untracked `scripts/claude-plan-review` file.
