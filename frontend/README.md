# frontend

ClarionPI workbench — Next.js 15 (App Router) + TypeScript + Tailwind + TanStack Query.
Landed at M3 Wave C (the first UI slice).

## Commands

```
npm install --cache "$(mktemp -d)"   # this machine's shared npm cache is unreliable
npm run dev        # next dev on :3400 (proxies /api/* -> 127.0.0.1:8400)
npm run typecheck  # tsc --noEmit
npm run lint       # next lint (next/core-web-vitals)
npm run test       # vitest run
npm run build      # next build
```

Run the backend (`make dev` at the repo root) on :8400 for live data; the dev proxy in
`next.config.ts` keeps `/api/*` same-origin so the session cookie stays first-party.

## Layout

- `lib/` — `api` (typed fetch + `ApiError`), `types` (backend view-model mirrors), `auth`
  (degrades to logged-out until the auth wave lands), `sse` (Phase-0 ingest stream),
  `query` (TanStack provider), `recent-matters` (client-side convenience only).
- `components/ui/` — hand-rolled shadcn-style primitives.
- `components/` — `MatterCreateForm`, `DeadlineBanner`, `GateStepper`, `DocumentsPanel`,
  `UserNav`, `RecentMattersList`.
- `app/` — `/` (workbench entry), `/login`, `/matters/[id]` (dashboard shell).

## Design rules honored (binding)

- Displays backend state, never invents it — no optimistic gate advancement; only a real
  `gate_ready` frame or a refetch moves the stepper.
- No gray-outs for legally-blocked actions — blocked actions stay clickable with an inline
  reason.
- Nothing token-shaped renders (the backend guarantees it; no FE detokenization).
- The deadline banner is non-dismissible by design (no close affordance).
