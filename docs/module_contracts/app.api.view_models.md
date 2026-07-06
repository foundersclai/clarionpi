# app.api.view_models

Backs [`system_contract.md`](../system_contract.md) invariants **8, 11, 12, 14**.
Module path: `backend/app/api`.
Design source: [`backlog/pi/components/api_and_wire.md`](../../backlog/pi/components/api_and_wire.md).

## Status

**Implemented @ M0 (partial).** The package exists with a `routes/` subpackage.
The SSE event vocabulary (`SseEvent`) and the input/view schemas
(`MatterCreate`, the entity views) are in `app/models`. The view-model builder,
the serializer token-scanner, the role middleware, and SSE journal replay land
**M3**.

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

- **[8]** Gate-action authorization is **server-side**; a required role maps to
  each gate action and the check precedes the handler — the frontend's
  `role_affordances` are a hint, the server is the authority.
- **[11]** Overlays are response-only; a serializer scanner (dev/test: 500 + loud
  log; prod: sentinel + log) guarantees **no token-shaped string escapes**;
  submit schemas are closed (`extra="forbid"`) so an overlay field in a request
  body is a `422`. Rendered previews carry a **span map (span_id → fact_id),
  never tokens**.
- **[12]** `budget_warning` at 80% rides the SSE vocabulary; the cap decision is
  surfaced from `app.core.matter_budget`, not re-derived here.
- **[14]** Every request logs into per-matter run logs (with `app/core`), so
  wire-level debugging starts from the log.

## Vocabulary

`GateEnvelope` (`gate` discriminant, `payload_version`, `view_model`,
`role_affordances`) · `RenderedLetterView` (`sections`, `span_map`) · `SseEvent`
(monotonic `id` for `Last-Event-ID` replay; the 7-event closed vocabulary) ·
`payload_version` skew → `409` → refetch · **import rule: nothing imports `api/`
except `main.py`** (the wire boundary is a leaf).

## Change rule

A boundary change requiring a contract update: adding/removing a REST route or
SSE event; changing a request/response shape or status code; changing the
serializer-scanner policy, the submit-schema closure, or the span-map contract;
changing the role→gate-action map or the tenancy-scoping injection. Adding
an `agent_reasoning`/`agent_thinking`-style event is **forbidden** by §11/§14.
Update this file **and** [`system_contract.md`](../system_contract.md) §8/11/12/14
in the same PR.
