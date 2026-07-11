# app.api.view_models

Backs [`system_contract.md`](../system_contract.md) invariants **7, 8, 11, 12, 14**.
Module path: `backend/app/api`.
Design source: [`backlog/pi/components/api_and_wire.md`](../../backlog/pi/components/api_and_wire.md).

## Status

**Live @ M3.** The wire surface is real: the gate envelope
(`routes/gates.py::get_current_gate` → `{gate, payload_version, view_model,
role_affordances}`) + the gate-action submit, the per-gate view-model builders
(`view_models.py::facts_review_vm` / `strategy_intake_vm` / `minimal_gate_vm`),
`role_affordances` (a side-effect-free dry-run guard preview), the wire
token-scanner (`wire_guard.scan_wire_payload`, dev/test-raise / prod-scrub),
closed submit schemas (`extra="forbid"`), 404-not-403 tenancy, and the matters
list endpoint. Auth + `require_role` (Wave A) are in `deps.py` (see
[ADR-0004](../adr/0004-m3-auth-decisions.md)).

**Extended @ M4.** The evidence-workbench (G2a) surface is live: the
`evidence_review` gate view-model (`view_models.py::evidence_review_vm`) and the
evidence routes (`routes/evidence.py` — exhibit picks, PHI disposition, manifest
read + EX-mint, source-row ledger read/edits, chronology overlays; `routes/analysis.py`
— the analysis SSE run + the risk-flag disposition). The analysis + late-docs runs
are the first SSE streams over the wire.

**Extended @ M5.** The drafting/compliance/package surface is live: the G2.5/G3/package
view-models (`view_models.py::plan_review_vm` / `compliance_review_vm` / `package_vm` +
`artifact_sets_view`) and the drafting routes (`routes/drafting.py` — plan emit, the demand
generate SSE run, the finding-action route, the package build SSE run, artifact list +
byte download). The demand + package runs are SSE (a `post_draft` compliance pre-check runs
INSIDE the demand stream); the compliance panel exposes each section's RENDERED preview,
never the tokenized body.

**Extended @ M6.** The provenance-viewer read surface is live (`routes/provenance.py`, two
routes; no view-model builder — both serialize directly): `GET /api/documents/{id}/blob`
(the app-served whole-document `application/pdf` bytes, `inline` `Content-Disposition`, a
`phi_access` audit row written BEFORE the bytes leave — the PHI byte-access event, inv 7 —
mirroring `get_artifact_download`; raw bytes, NOT wire-scanned) and
`GET /api/matters/{id}/provenance/{token_id}` (a BARE token id → `{token_id, display_form,
outcome, source, anchors[]}`, each anchor `{document_id, page, bbox, blob_url, page_count,
superseded}` with `bbox` always `null` at v1 — page-level highlights; NO audit here, the token
lookup is not the PHI event; wire-scanned, inv 11). This realizes the render-span map reaching
the FE viewer (the deferred M5 line): the compliance panel's BARE-id `spans` click through to
this route.

**Deferred:** SSE journal / `Last-Event-ID` replay is still deferred — the gates wire is
request/response, and the analysis/ingest/demand/package streams are fire-and-forward (no
journal). The scanner is applied **explicitly** per response (every gate envelope AND every
evidence/analysis/drafting/provenance JSON response); promoting it to a response middleware is
still planned. The rendered-letter span map (span_id → fact_id) now reaches the FE viewer at
**M6** via the provenance route (the render spans persist on `DraftSection.spans` and click
through to `GET /api/matters/{id}/provenance/{token_id}`).

## Responsibility

The **only wire surface** — every byte between backend and frontend crosses here.
REST endpoints + SSE streams exactly per
[`04 §3–4`](../../backlog/pi/04_data_model_and_contracts.md); nothing else
serializes to the frontend. Routes are **thin**: validate the request, check role
+ tenancy, call the owning engine component, serialize a view-model. AI overlays
exist **only in view-models on responses**; submissions never echo them back.

**Not responsible for:** business logic (routes are thin); state transitions
(`app.engine.orchestrator`); rendering/detokenization (`app.package.builder`);
minting or resolving tokens (`app.engine.tokenizer`).

## Owns / Consumes / Produces

| Direction | Item | Counterpart |
|---|---|---|
| Owns | REST route layer, `view_models`, SSE emitter + journal replay, serializer scanner | — |
| Consumes | every engine component's typed outputs (facts, ledger, findings, artifacts) | all engine components |
| Consumes | gate-current payloads + transition results | app.engine.orchestrator |
| Consumes | auth/tenancy context, run journal, presign | app/core (cross-cutting) |
| Produces | HTTP responses (view-models, discriminated gate payloads) | frontend |
| Produces | SSE streams (`status`, `doc_state`, `section`, `gate_ready`, `artifact_ready`, `budget_warning`, `error`) | frontend |
| Produces | provenance token→anchor lookups (`provenance/{token_id}`) + app-served document blobs (`documents/{id}/blob`) | frontend (viewer) |

## Invariants enforced

- **[7]** The M6 blob route (`get_document_blob`) is a PHI byte-access surface: every
  fetch that returns bytes writes a committed `phi_access` audit row (actor + `document_id`
  + `surface`) **before** the bytes leave — mirroring the M5 `get_artifact_download`
  precedent. The token/metadata lookup (`get_token_provenance`) is deliberately **unaudited**
  (it returns anchor metadata, not PHI bytes). The bytes are tenant-scoped (a cross-firm
  document 404s) and served `inline`; the object store stays inside the envelope.
- **[8]** Gate-action authorization is **server-side** (M3): `require_role`
  (`deps.py`) guards the door and the service re-derives the actor role onto
  `GateRecord.actor_role`; a cross-firm matter **404s, never 403s** (existence must
  not leak). The frontend's `role_affordances` (`can_edit`, `can_approve`,
  `approve_blockers` — a side-effect-free guard dry-run) are a hint; the server is
  the authority. A role refusal is a typed `403 role_forbidden`.
- **[11]** Overlays are response-only; the wire token-scanner
  (`wire_guard.scan_wire_payload` — dev/test: raise `TokenLeak` → 500 + loud log;
  prod: registry `SENTINEL` + `clarionpi.wire` ERROR log) guarantees **no
  token-shaped string escapes**, applied explicitly on every gate envelope; submit
  schemas are closed (`extra="forbid"`) so an overlay field echoed in a request
  body is a `422`. Rendered previews (M5) will carry a **span map (span_id →
  fact_id), never tokens**.
- **[12]** `budget_warning` at 80% rides the SSE vocabulary; the cap decision is
  surfaced from `app.core.matter_budget`, not re-derived here.
- **[14]** Every request logs into per-matter run logs (with `app/core`), so
  wire-level debugging starts from the log.

## Vocabulary

Gate envelope `{gate, payload_version, view_model, role_affordances}` (M3, a
JSON-safe dict — heterogeneous per gate, so the scanner walks it before it leaves)
· view-model builders `facts_review_vm` (deadline candidates + `rule_id`
[=`statute_cite`] + intake facts + `documents_summary`) / `strategy_intake_vm`
(`StrategyInputs` values incl. the M4 pull-forward `mmi_date` /
`property_damage_estimate_cents`) / `evidence_review_vm` (M4 — the G2a payload
`{chronology, ledger, risk_flags, exhibits, dedup_pending}`; **GET never spends LLM
budget** — chronology is rebuilt `generate_narratives=False` and no manifest tokens
are minted; ledger serialization is integer cents only and surfaces
`missing_paid_line_ids` / `excluded_line_ids` — a gap is shown, never swallowed;
tokens resolved to display forms so nothing token-shaped survives; `None` ledger when
the jurisdiction pack is unsupported) / `minimal_gate_vm` (honest placeholder) ·
**M5 gate view-models** (all budget-free; nothing token-shaped survives):
`plan_review_vm` (`{plan, plan_missing, registry_version_current}` — the latest
`StrategyPlan` view or `None`; the FE compares the plan's `registry_version` to
`registry_version_current` to surface plan-level drift) / `compliance_review_vm`
(`{draft, sections, findings, open_blocking, buckets}` — `draft` is
id/version/registry_version/status/memo; `sections` carry the RENDERED preview + BARE-id
spans, **never** the tokenized body (inv 11); `findings` are `ComplianceFindingView`s
ordered blocking-first then oldest; `open_blocking` is the exact G3-guard count;
`buckets` = `{mechanical, semantic}` over the OPEN findings) / `package_vm`
(`{artifact_sets, buildable}` — `buildable` true only at `package_assembly` when the
latest draft is `approved`) · `artifact_sets_view` (latest first; each artifact is
`{kind, sha256, byte_count, url}` — the `object_key` is INTERNAL and never on the wire,
only the kind-keyed download `url`) · **drafting-route error vocabulary**:
`matter_not_found`/`finding_not_found`/`artifact_not_found` → `404`,
`gate_state_mismatch` → `409`, `letter_structure_missing` → `422`,
`hard_block_not_disposable` → `409`, `role_forbidden` → `403`,
`disposition_reason_required`/`disposition_action_not_supported` → `422`; the demand SSE
converts a structural `post_draft` escape (`compliance_snapshot_drift` /
`draft_registry_drift`) into a trailing `error` frame, and the package SSE surfaces
`binder_blocked` / `artifact_token_leak` / `binder_page_missing` / `no_draft` as `error`
frames with NO advance (the state unchanged) · **M6 provenance routes** (`routes/provenance.py`,
serialize-direct, no view-model builder): `GET /api/documents/{id}/blob` → raw
`application/pdf` bytes, `inline` `Content-Disposition` (sanitized filename), a `phi_access`
audit row written+committed BEFORE the bytes (the PHI byte-access event, inv 7; a raw-bytes
`Response`, deliberately NOT wire-scanned) — errors `document_not_found` / `blob_missing` →
`404`; `GET /api/matters/{id}/provenance/{token_id}` → `{token_id, display_form, outcome,
source, anchors[]}` where each anchor is `{document_id, page, bbox, blob_url, page_count,
superseded}` (`bbox` always `null` at v1 — page-level highlights; `blob_url` is the
ready-to-fetch `documents/{id}/blob` path so the FE never constructs it; resolved WITHOUT a
`live_ledger_hash` — the viewer shows provenance, not the G3 amount-drift verdict; NO audit
here — the token lookup is not the PHI event; wire-scanned, inv 11) — the accepted id is the
BARE registry grammar `^(FACT|AMT|CITE|EX)_\d+$` (a bracketed/lower-case shape is rejected too),
errors `invalid_token_id` → `422` (malformed) / `token_not_found` → `404` (well-formed but
unknown) / `matter_not_found` → `404` (cross-firm, existence not leaked) ·
**upload views** (`UploadSessionView`/`UploadSlotView` — each slot carries a stable
`ordinal`, its zero-based registration order and the client's ONLY sanctioned pairing key;
slots are served in ordinal order on register and resume) · **upload-route error
vocabulary** (SEC-05): registration over a configured bound →
`413 {error: upload_limit_exceeded, limit: max_files | max_file_bytes | max_session_bytes}`;
a slot PUT crossing the per-file cap → the same `413` with `limit: max_file_bytes`, streamed
and stopped mid-body (the body is never read whole into memory); an actual-vs-declared byte
mismatch → `422 {error: upload_size_mismatch}`; a non-OPEN session →
`409 {error: upload_session_not_open, status}` (pre-checked before the body is consumed) —
on every refusal the slot's prior object and `received` state are untouched · **CSRF
refusal** (SEC-03): every unsafe-method request (`POST`/`PUT`/`PATCH`/`DELETE`, login and
logout included) under session-mode enforcement requires exactly ONE `Origin` header
exactly matching a configured trusted origin — missing/duplicate/malformed/`null`/untrusted
→ `403 {error: csrf_failed}` from the ASGI middleware before any route handler ·
`role_affordances` (`can_edit`, `can_approve`, `approve_blockers`) ·
`scan_wire_payload(where=...)` → `TokenLeak` · closed submit schemas
(`extra="forbid"`) · `payload_version` skew → `409` → refetch · **import rule:
nothing imports `api/` except `main.py`** (the wire boundary is a leaf). `SseEvent`
(the closed, no-internal-reasoning vocabulary) exists in `app/models`; its
monotonic-`id` `Last-Event-ID` **replay is deferred to the analysis/demand streams
(M4/M5)** · `RenderedLetterView` (`sections`, `span_map`) lands **M5**.

## Change rule

A boundary change requiring a contract update: adding/removing a REST route or
SSE event (incl. the M4 evidence/analysis routes, the M5 drafting routes — plan
emit, demand generate, finding action, package build, artifact list/download — and
the M6 provenance routes — `documents/{id}/blob`, `matters/{id}/provenance/{token_id}` —
and their typed error shapes); changing a request/response shape or status code (incl. the
gate envelope, a per-gate view-model builder's shape, the `evidence_review_vm` key set /
its GET-never-spends-LLM + ledger-serialization rules, the M5 `plan_review_vm` /
`compliance_review_vm` / `package_vm` key sets, the `artifact_sets_view`
object_key-never-on-wire rule, the drafting-route error vocabulary, or the M6 provenance
anchor shape / bare-id grammar / the blob route's audited-before-bytes + inline-bytes rule);
changing the
wire-scanner
policy (raise/scrub, or where it is applied), the submit-schema closure, the
`role_affordances` contract, or the span-map contract; changing the
role→gate-action map or the tenancy-scoping injection (incl. the 404-not-403
rule). Adding an `agent_reasoning`/`agent_thinking`-style event is **forbidden**
by §11/§14. Update this file **and** [`system_contract.md`](../system_contract.md)
§8/11/12/14 in the same PR.
