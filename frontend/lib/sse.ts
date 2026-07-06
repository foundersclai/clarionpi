/**
 * SSE client for the Phase-0 ingest stream.
 *
 * `POST /api/matters/{id}/ingest/run` returns a `text/event-stream` whose frames are
 * `event: <name>\ndata: <json>\n\n`. The event name is the CLOSED backend vocabulary
 * (SseEvent in enums.py): status | doc_state | section | gate_ready | artifact_ready |
 * budget_warning | error. We type that union so the caller switches over known names only.
 *
 * isRunning pattern: `runIngest` resolves when the stream ends (or aborts). The caller keys
 * UI on run-active — flip a flag true before awaiting, false in a finally. Only a real
 * `gate_ready` frame (or a refetch after the terminal `status:completed`) may advance the
 * gate stepper: the frontend displays backend state, it never optimistically advances.
 */

import { fetchEventSource } from "@microsoft/fetch-event-source";

/** The closed SSE event-name vocabulary (SseEvent in the backend). */
export type SseEventName =
  | "status"
  | "doc_state"
  | "section"
  | "gate_ready"
  | "artifact_ready"
  | "budget_warning"
  | "error";

/** The set form, for a runtime membership check when parsing frames. */
export const SSE_EVENT_NAMES: ReadonlySet<SseEventName> = new Set<SseEventName>([
  "status",
  "doc_state",
  "section",
  "gate_ready",
  "artifact_ready",
  "budget_warning",
  "error",
]);

/** One parsed SSE frame. `data` is the JSON payload (shape varies by event). */
export interface SseFrame {
  event: SseEventName;
  data: unknown;
}

/**
 * Parse a raw SSE frame block (`event:`/`data:` lines) into an {@link SseFrame}, or `null`
 * if the event name is outside the closed vocabulary. Exported for unit testing the parser
 * independently of the network. Per the SSE spec, `data:` lines concatenate with `\n`.
 */
export function parseSseFrame(
  eventName: string | undefined,
  rawData: string,
): SseFrame | null {
  if (eventName === undefined || !SSE_EVENT_NAMES.has(eventName as SseEventName)) {
    return null;
  }
  let data: unknown = rawData;
  try {
    data = rawData.length > 0 ? JSON.parse(rawData) : null;
  } catch {
    data = rawData; // leave non-JSON payloads as the raw string
  }
  return { event: eventName as SseEventName, data };
}

/** Options for {@link runIngest}. */
export interface RunIngestOptions {
  /** Called once per in-vocabulary frame, in arrival order. */
  onEvent: (frame: SseFrame) => void;
  /** Aborts the stream when triggered (e.g. component unmount). */
  signal?: AbortSignal;
}

/**
 * Run Phase-0 ingest for `matterId`, streaming frames to `onEvent`. Resolves when the
 * stream closes; rejects only on a connection-level failure (not on an in-band `error`
 * frame — that arrives via `onEvent` so the caller renders it inline).
 */
export async function runIngest(
  matterId: string,
  { onEvent, signal }: RunIngestOptions,
): Promise<void> {
  await fetchEventSource(`/api/matters/${matterId}/ingest/run`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "text/event-stream" },
    signal,
    // Suppress the library's default retry-on-close: this is a one-shot job stream, and a
    // reconnect would re-POST the ingest run. Throwing here ends the stream cleanly.
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
      // Rethrow to abort — the caller's try/catch surfaces the connection failure. Not
      // rethrowing would make fetch-event-source retry forever.
      throw err;
    },
  });
}
