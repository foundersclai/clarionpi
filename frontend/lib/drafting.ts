"use client";

/**
 * Drafting / compliance / package client (M5: plan_review → drafting → compliance_review →
 * package_*) — the fetchers, the two SSE runners (demand generation + package build), the
 * finding-action mutation, and the TanStack hooks the four screens drive.
 *
 * Wire disciplines carried here (binding):
 *   - The two SSE runners mirror `lib/evidence.ts::runAnalysis` (POST → text/event-stream,
 *     one-shot, no auto-reconnect). ONLY a real `gate_ready` frame (or a refetch after the
 *     terminal `status:completed`) may advance the gate — the FE displays backend state, it
 *     never optimistically advances it.
 *   - `emitPlan` (non-SSE — a single bounded Opus call, seconds-long) and `findingAction`
 *     invalidate the ["gate", matterId] envelope on success, so each screen re-reads the
 *     authoritative view-model rather than patching state locally.
 *   - Submit bodies are CLOSED — `FindingActionBody` mirrors the backend's `extra="forbid"`
 *     model. Money is not sent here (the plan-edit money path rides the gates `edit` submit).
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
  ArtifactsResponse,
  FindingActionBody,
  FindingActionResponse,
  PlanEmitResponse,
} from "@/lib/types";

/** Query key for a matter's artifact sets (the package_ready download list). */
export const artifactsKey = (matterId: string) => ["artifacts", matterId] as const;

// ---------------------------------------------------------------------------------------
// Fetchers (thin apiGet / apiPost wrappers).
// ---------------------------------------------------------------------------------------

/** POST the G2.5 plan emit (runs the strategist). Non-SSE: resolves to the fresh unapproved plan. */
export function emitPlan(matterId: string): Promise<PlanEmitResponse> {
  return apiPost<PlanEmitResponse>(`/api/matters/${matterId}/plan/emit`);
}

/** POST one attorney action on a G3 finding (patch / regen / accept / override). */
export function findingAction(
  findingId: string,
  body: FindingActionBody,
): Promise<FindingActionResponse> {
  return apiPost<FindingActionResponse>(`/api/findings/${findingId}/action`, body);
}

/** GET the matter's artifact sets (latest first). */
export function getArtifacts(matterId: string): Promise<ArtifactsResponse> {
  return apiGet<ArtifactsResponse>(`/api/matters/${matterId}/artifacts`);
}

// ---------------------------------------------------------------------------------------
// SSE runners (mirror lib/evidence.ts::runAnalysis — one-shot POST stream, no reconnect).
// ---------------------------------------------------------------------------------------

/** Options for the SSE runners — `onEvent` fires once per in-vocabulary frame, in arrival order. */
export interface RunStreamOptions {
  onEvent: (frame: SseFrame) => void;
  /** Aborts the stream when triggered (e.g. component unmount). */
  signal?: AbortSignal;
}

async function runStream(url: string, { onEvent, signal }: RunStreamOptions): Promise<void> {
  await fetchEventSource(url, {
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

/**
 * Run the Brain-2 demand generation for `matterId`, streaming frames to `onEvent`. Resolves when
 * the stream closes; rejects only on a connection-level failure (an in-band `error` frame arrives
 * via `onEvent`, rendered inline). A per-section `section` frame carries the rendered preview; a
 * terminal `status:draft_incomplete` names the failed sections; a real `gate_ready` advances.
 */
export function runDemandGeneration(matterId: string, opts: RunStreamOptions): Promise<void> {
  return runStream(`/api/matters/${matterId}/demand/generate`, opts);
}

/**
 * Run the package build for `matterId`, streaming frames to `onEvent`. Resolves when the stream
 * closes; rejects only on a connection-level failure. One `artifact_ready` frame per artifact; a
 * `binder_blocked` / `artifact_token_leak` / `binder_page_missing` / `no_draft` ERROR frame is a
 * blocked build (no advance); a real `gate_ready` advances to package_ready.
 */
export function runPackageBuild(matterId: string, opts: RunStreamOptions): Promise<void> {
  return runStream(`/api/matters/${matterId}/package/build`, opts);
}

// ---------------------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------------------

/**
 * Mutation wrapping {@link emitPlan}. On success it invalidates the ["gate", matterId] envelope so
 * the plan-review card re-reads the freshly emitted (unapproved) plan from backend state.
 */
export function useEmitPlan(
  matterId: string,
): UseMutationResult<PlanEmitResponse, ApiError, void> {
  const queryClient = useQueryClient();
  return useMutation<PlanEmitResponse, ApiError, void>({
    mutationFn: () => emitPlan(matterId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: gateKey(matterId) });
    },
  });
}

/**
 * Mutation wrapping {@link findingAction}. On success it invalidates the ["gate", matterId]
 * envelope so the compliance panel re-reads the finding lifecycle + the open-blocking count.
 */
export function useFindingAction(
  matterId: string,
): UseMutationResult<FindingActionResponse, ApiError, { findingId: string; body: FindingActionBody }> {
  const queryClient = useQueryClient();
  return useMutation<
    FindingActionResponse,
    ApiError,
    { findingId: string; body: FindingActionBody }
  >({
    mutationFn: ({ findingId, body }) => findingAction(findingId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: gateKey(matterId) });
    },
  });
}

/** TanStack query for the matter's artifact sets. `enabled` gates it to the package screens. */
export function useArtifacts(
  matterId: string,
  enabled: boolean,
): UseQueryResult<ArtifactsResponse, ApiError> {
  return useQuery<ArtifactsResponse, ApiError>({
    queryKey: artifactsKey(matterId),
    queryFn: () => getArtifacts(matterId),
    enabled,
  });
}
