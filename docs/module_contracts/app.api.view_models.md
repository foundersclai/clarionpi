# app.api.view_models

Backs [`system_contract.md`](../system_contract.md) invariants **8, 11, 12, 14**.
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

**Deferred:** SSE journal / `Last-Event-ID` replay lands with the analysis/demand
streams (**M4/M5**) — the gates wire is request/response, so no journal ships this
wave. The G2a view-model lands **M4**; the G2.5/G3 view-models land **M5** (today
they are the honest `minimal_gate_vm` placeholder). The scanner is applied
**explicitly** per response envelope at M3; promoting it to a response middleware
is planned **M4+**.

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
| Produces | provenance span→fact anchor lookups | frontend (viewer) |

## Invariants enforced

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
`property_damage_estimate_cents`) / `minimal_gate_vm` (honest placeholder) ·
`role_affordances` (`can_edit`, `can_approve`, `approve_blockers`) ·
`scan_wire_payload(where=...)` → `TokenLeak` · closed submit schemas
(`extra="forbid"`) · `payload_version` skew → `409` → refetch · **import rule:
nothing imports `api/` except `main.py`** (the wire boundary is a leaf). `SseEvent`
(the closed, no-internal-reasoning vocabulary) exists in `app/models`; its
monotonic-`id` `Last-Event-ID` **replay is deferred to the analysis/demand streams
(M4/M5)** · `RenderedLetterView` (`sections`, `span_map`) lands **M5**.

## Change rule

A boundary change requiring a contract update: adding/removing a REST route or
SSE event; changing a request/response shape or status code (incl. the gate
envelope or a per-gate view-model builder's shape); changing the wire-scanner
policy (raise/scrub, or where it is applied), the submit-schema closure, the
`role_affordances` contract, or the span-map contract; changing the
role→gate-action map or the tenancy-scoping injection (incl. the 404-not-403
rule). Adding an `agent_reasoning`/`agent_thinking`-style event is **forbidden**
by §11/§14. Update this file **and** [`system_contract.md`](../system_contract.md)
§8/11/12/14 in the same PR.
