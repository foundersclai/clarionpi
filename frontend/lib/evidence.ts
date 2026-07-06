"use client";

/**
 * Evidence-workbench client (G2a / evidence_review) — the fetchers, TanStack mutations, and the
 * analysis SSE runner the workbench drives.
 *
 * Two wire disciplines carried here:
 *   - EVERY mutation invalidates the ["gate", matterId] envelope on success, so the workbench
 *     re-reads the authoritative view-model (chronology / ledger / flags / exhibits) rather than
 *     patching state locally. The billing-edit mutation ALSO returns the recomputed ledger so the
 *     grid can replace its display from the server total in the same round-trip (the FE never sums).
 *   - Submit bodies are CLOSED — each body type mirrors exactly the backend's `extra="forbid"`
 *     model. Money crosses IN as dollar strings (empty string clears); nothing token-shaped moves.
 *
 * The analysis SSE runner mirrors `lib/sse.ts::runIngest` (POST → text/event-stream, one-shot,
 * no auto-reconnect). ONLY a real `gate_ready` frame (or a refetch after `status:completed`) may
 * advance the gate — the FE displays backend state, never optimistically advances it.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import { gateKey } from "@/lib/gates";
import { parseSseFrame, type SseFrame } from "@/lib/sse";
import type {
  BillingEditBatch,
  BillingEditResponse,
  BillingLinesResponse,
  ChronologyOverlayBody,
  ExhibitPickBody,
  ExhibitView,
  FlagDispositionBody,
  ManifestResponse,
  PhiDispositionBody,
  RiskFlagVM,
} from "@/lib/types";

// ---------------------------------------------------------------------------------------
// Fetchers (thin apiGet/apiPost wrappers; PUT via fetch since the api helper has no apiPut).
// ---------------------------------------------------------------------------------------

/** Query key for a matter's source billing lines (the ledger "Edit lines" grid). */
export const billingLinesKey = (matterId: string) => ["billing-lines", matterId] as const;

/** PUT `body` (JSON) to `path`, returning the typed JSON body. Throws {@link ApiError} on non-2xx. */
async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "PUT",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  const parsed: unknown = text.length === 0 ? null : safeJson(text);
  if (!response.ok) {
    const errBody =
      parsed !== null && typeof parsed === "object" ? parsed : { detail: String(parsed) };
    throw new ApiError(response.status, errBody as Record<string, unknown>);
  }
  return parsed as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

/** GET the source billing lines for the ledger edit grid (PINNED sibling contract). */
export function getBillingLines(matterId: string): Promise<BillingLinesResponse> {
  return apiGet<BillingLinesResponse>(`/api/matters/${matterId}/billing/lines`);
}

/** GET the draft binder manifest; `mint` mints EX tokens first. */
export function getManifest(matterId: string, mint: boolean): Promise<ManifestResponse> {
  return apiGet<ManifestResponse>(
    `/api/matters/${matterId}/manifest${mint ? "?mint=true" : ""}`,
  );
}

// ---------------------------------------------------------------------------------------
// Hooks — reads
// ---------------------------------------------------------------------------------------

/** TanStack query for the source billing lines. `enabled` is false until the grid is expanded. */
export function useBillingLines(
  matterId: string,
  enabled: boolean,
): UseQueryResult<BillingLinesResponse, ApiError> {
  return useQuery<BillingLinesResponse, ApiError>({
    queryKey: billingLinesKey(matterId),
    queryFn: () => getBillingLines(matterId),
    enabled,
  });
}

// ---------------------------------------------------------------------------------------
// Hooks — mutations (all invalidate the gate envelope on success)
// ---------------------------------------------------------------------------------------

/** PUT a risk-flag disposition. On success the envelope refetches (the flag list re-reads). */
export function useFlagDisposition(
  matterId: string,
): UseMutationResult<RiskFlagVM, ApiError, { flagId: string; body: FlagDispositionBody }> {
  const queryClient = useQueryClient();
  return useMutation<RiskFlagVM, ApiError, { flagId: string; body: FlagDispositionBody }>({
    mutationFn: ({ flagId, body }) =>
      apiPut<RiskFlagVM>(`/api/flags/${flagId}/disposition`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: gateKey(matterId) });
    },
  });
}

/** PUT an exhibit pick (page include/exclude + order). On success the envelope refetches. */
export function useExhibitPick(
  matterId: string,
): UseMutationResult<ExhibitView, ApiError, ExhibitPickBody> {
  const queryClient = useQueryClient();
  return useMutation<ExhibitView, ApiError, ExhibitPickBody>({
    mutationFn: (body) => apiPut<ExhibitView>(`/api/matters/${matterId}/exhibits`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: gateKey(matterId) });
    },
  });
}

/** POST a PHI disposition on one exhibit (attorney-only; 403 typed otherwise). Envelope refetches. */
export function usePhiDisposition(
  matterId: string,
): UseMutationResult<ExhibitView, ApiError, { exhibitId: string; body: PhiDispositionBody }> {
  const queryClient = useQueryClient();
  return useMutation<ExhibitView, ApiError, { exhibitId: string; body: PhiDispositionBody }>({
    mutationFn: ({ exhibitId, body }) =>
      apiPost<ExhibitView>(`/api/exhibits/${exhibitId}/phi`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: gateKey(matterId) });
    },
  });
}

/** PUT a chronology-row overlay (closed four-field vocabulary). Envelope refetches. */
export function useChronologyOverlay(
  matterId: string,
): UseMutationResult<unknown, ApiError, { encounterId: string; body: ChronologyOverlayBody }> {
  const queryClient = useQueryClient();
  return useMutation<unknown, ApiError, { encounterId: string; body: ChronologyOverlayBody }>({
    mutationFn: ({ encounterId, body }) =>
      apiPut(`/api/matters/${matterId}/chronology/${encounterId}/overlay`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: gateKey(matterId) });
    },
  });
}

/**
 * POST a batch of source-row billing edits. On success the envelope AND the billing-lines grid
 * both refetch; the caller ALSO reads the returned `ledger` off the mutation result to replace the
 * displayed ledger from the server total in the same round-trip (invariant 10 — the FE never sums).
 */
export function useBillingEdits(
  matterId: string,
): UseMutationResult<BillingEditResponse, ApiError, BillingEditBatch> {
  const queryClient = useQueryClient();
  return useMutation<BillingEditResponse, ApiError, BillingEditBatch>({
    mutationFn: (batch) =>
      apiPost<BillingEditResponse>(`/api/matters/${matterId}/billing/edits`, batch),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: gateKey(matterId) });
      void queryClient.invalidateQueries({ queryKey: billingLinesKey(matterId) });
    },
  });
}

// ---------------------------------------------------------------------------------------
// Analysis SSE runner (mirrors lib/sse.ts::runIngest — one-shot POST stream, no reconnect).
// ---------------------------------------------------------------------------------------

/** Options for {@link runAnalysis}. */
export interface RunAnalysisOptions {
  /** Called once per in-vocabulary frame, in arrival order. */
  onEvent: (frame: SseFrame) => void;
  /** Aborts the stream when triggered (e.g. component unmount). */
  signal?: AbortSignal;
}

/**
 * Run (or re-run) the Brain-1 analysis for `matterId`, streaming frames to `onEvent`. Resolves
 * when the stream closes; rejects only on a connection-level failure (an in-band `error` frame
 * arrives via `onEvent`, rendered inline). At `analysis_running` this is the first run; at
 * `evidence_review` the backend treats the same POST as a re-run.
 */
export async function runAnalysis(
  matterId: string,
  { onEvent, signal }: RunAnalysisOptions,
): Promise<void> {
  await fetchEventSource(`/api/matters/${matterId}/analysis/run`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "text/event-stream" },
    signal,
    openWhenHidden: true,
    onmessage(message) {
      const frame = parseSseFrame(message.event, message.data);
      if (frame !== null) {
        onEvent(frame);
      }
    },
    onclose() {
      // Server closed the stream normally (run finished). Do not reconnect.
    },
    onerror(err) {
      // Rethrow to abort — the caller's try/catch surfaces the failure (else the lib retries forever).
      throw err;
    },
  });
}
