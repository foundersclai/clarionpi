"use client";

/**
 * Gates client — the current-gate envelope + the gate-action submit, plus the TanStack
 * hooks the G1 / G1.5 cards drive.
 *
 * Wire discipline (binding): the submit body carries ONLY the closed GateSubmit keys (no
 * overlay / view-model echo); `idempotency_key` is minted client-side per ATTEMPT via
 * `crypto.randomUUID()` (a fresh key each try so a retry after a network blip does not
 * accidentally replay — a genuine duplicate is the caller re-submitting the SAME key, which
 * we never do here). The UUID (36 chars) sits inside the backend's 8..64 bound.
 *
 * Stale-fence handling: a submit that races a state change comes back 409
 * `stale_payload_version` / `gate_state_mismatch`. The mutation AUTO-REFETCHES the envelope
 * (so the card redraws against fresh backend state) and rethrows a typed {@link GateStaleError}
 * the UI renders as "This gate changed — refreshed; review and retry." The FE never guesses
 * the new state — it re-reads it.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import type {
  GateEnvelope,
  GateState,
  GateSubmitBody,
  GateSubmitResult,
} from "@/lib/types";

/** Query key for a matter's current-gate envelope. */
export const gateKey = (matterId: string) => ["gate", matterId] as const;

/** The refusal codes that mean "the envelope you submitted against is out of date". */
const STALE_FENCE_CODES = new Set(["stale_payload_version", "gate_state_mismatch"]);

/**
 * Thrown after a stale-fence 409 has triggered an envelope refetch. Carries the original
 * ApiError so a caller that wants the raw code/detail still has it; `message` is the copy the
 * UI shows by default.
 */
export class GateStaleError extends Error {
  readonly cause: ApiError;
  constructor(cause: ApiError) {
    super("This gate changed — refreshed; review and retry.");
    this.name = "GateStaleError";
    this.cause = cause;
  }
}

/** Mint a fresh idempotency key for one submit attempt (36-char UUID; within 8..64). */
export function mintIdempotencyKey(): string {
  // randomUUID is 36 chars; slice defensively in case a polyfill returns a longer value.
  return crypto.randomUUID().slice(0, 36);
}

/** GET the current-gate envelope for a matter. Throws {@link ApiError} on a refusal. */
export function getCurrentGate(matterId: string): Promise<GateEnvelope> {
  return apiGet<GateEnvelope>(`/api/matters/${matterId}/gates/current`);
}

/**
 * POST a gate action. The caller passes the body WITHOUT `idempotency_key` — it is minted
 * here per attempt so no call site can forget it or reuse one. Returns the success body.
 */
export function submitGate(
  matterId: string,
  gate: GateState,
  body: Omit<GateSubmitBody, "idempotency_key">,
): Promise<GateSubmitResult> {
  const full: GateSubmitBody = { ...body, idempotency_key: mintIdempotencyKey() };
  return apiPost<GateSubmitResult>(`/api/matters/${matterId}/gates/${gate}/submit`, full);
}

/** TanStack query for the current-gate envelope (queryKey ["gate", matterId]). */
export function useGate(matterId: string): UseQueryResult<GateEnvelope, ApiError> {
  return useQuery<GateEnvelope, ApiError>({
    queryKey: gateKey(matterId),
    queryFn: () => getCurrentGate(matterId),
  });
}

/** Variables for {@link useSubmitGate}: the gate path + the body (sans idempotency_key). */
export interface SubmitGateVars {
  gate: GateState;
  body: Omit<GateSubmitBody, "idempotency_key">;
}

/**
 * Mutation wrapping {@link submitGate}. On success it invalidates the ["gate"] and ["matter"]
 * query families (so the envelope and the matter dashboard both re-read backend state). On a
 * stale-fence 409 it refetches THIS matter's envelope and rethrows a {@link GateStaleError};
 * any other ApiError propagates unchanged for the card to render inline (verbatim body).
 */
export function useSubmitGate(
  matterId: string,
): UseMutationResult<GateSubmitResult, ApiError | GateStaleError, SubmitGateVars> {
  const queryClient = useQueryClient();
  return useMutation<GateSubmitResult, ApiError | GateStaleError, SubmitGateVars>({
    mutationFn: async ({ gate, body }) => {
      try {
        return await submitGate(matterId, gate, body);
      } catch (error) {
        if (
          error instanceof ApiError &&
          typeof error.body.error === "string" &&
          STALE_FENCE_CODES.has(error.body.error)
        ) {
          // Re-read the authoritative envelope (fetchQuery ALWAYS fires and writes the cache,
          // updating any mounted observer), then surface a typed "changed" error. Unconditional
          // by design: the gate moved under us, so the FE re-reads it rather than guessing.
          await queryClient.fetchQuery({
            queryKey: gateKey(matterId),
            queryFn: () => getCurrentGate(matterId),
          });
          throw new GateStaleError(error);
        }
        throw error;
      }
    },
    onSuccess: () => {
      // The envelope AND the matter view both moved — re-read both families from backend.
      void queryClient.invalidateQueries({ queryKey: ["gate"] });
      void queryClient.invalidateQueries({ queryKey: ["matter"] });
    },
  });
}
