import { describe, expect, it } from "vitest";
import {
  SSE_EVENT_NAMES,
  parseSseFrame,
  type SseEventName,
  type SseFrame,
} from "@/lib/sse";

/**
 * Split a raw multi-frame SSE chunk (`event:`/`data:` blocks separated by a blank line)
 * into ordered frames via the parser. This mirrors how the transport hands us one frame at
 * a time, but proves the parser produces the right ordered, in-vocabulary events from a
 * realistic phase0 chunk.
 */
function parseChunk(chunk: string): SseFrame[] {
  const frames: SseFrame[] = [];
  for (const block of chunk.trim().split("\n\n")) {
    let eventName: string | undefined;
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice("event:".length).trim();
      } else if (line.startsWith("data:")) {
        // Per SSE spec, multiple data: lines concatenate with \n.
        dataLines.push(line.slice("data:".length).trimStart());
      }
    }
    const frame = parseSseFrame(eventName, dataLines.join("\n"));
    if (frame) frames.push(frame);
  }
  return frames;
}

describe("parseSseFrame", () => {
  it("parses a single frame's JSON data", () => {
    const frame = parseSseFrame("doc_state", '{"document_id":"abc","status":"classifying"}');
    expect(frame).not.toBeNull();
    expect(frame?.event).toBe("doc_state");
    expect(frame?.data).toEqual({ document_id: "abc", status: "classifying" });
  });

  it("rejects an event name outside the closed vocabulary", () => {
    // agent_reasoning is exactly the kind of event the backend forbids — the parser drops it.
    expect(parseSseFrame("agent_reasoning", '{"x":1}')).toBeNull();
    expect(parseSseFrame(undefined, "{}")).toBeNull();
  });

  it("leaves non-JSON data as the raw string", () => {
    const frame = parseSseFrame("status", "not-json");
    expect(frame?.data).toBe("not-json");
  });

  it("accepts every name in the closed union", () => {
    const names: SseEventName[] = [
      "status",
      "doc_state",
      "section",
      "gate_ready",
      "artifact_ready",
      "budget_warning",
      "error",
    ];
    for (const name of names) {
      expect(SSE_EVENT_NAMES.has(name)).toBe(true);
      expect(parseSseFrame(name, "{}")).not.toBeNull();
    }
  });
});

describe("multi-frame chunk parsing", () => {
  it("produces ordered frames from a realistic phase0 chunk", () => {
    const chunk = [
      'event: status\ndata: {"phase":"phase0","state":"started","pending_documents":1}',
      'event: doc_state\ndata: {"document_id":"d1","status":"classifying"}',
      'event: doc_state\ndata: {"document_id":"d1","status":"extracted"}',
      'event: gate_ready\ndata: {"gate":"facts_review","matter_id":"m1"}',
      'event: status\ndata: {"phase":"phase0","state":"completed"}',
    ].join("\n\n");

    const frames = parseChunk(chunk);

    expect(frames.map((f) => f.event)).toEqual([
      "status",
      "doc_state",
      "doc_state",
      "gate_ready",
      "status",
    ]);
    expect((frames[3].data as Record<string, unknown>).gate).toBe("facts_review");
  });

  it("drops out-of-vocabulary frames but keeps order of the rest", () => {
    const chunk = [
      'event: status\ndata: {"state":"started"}',
      'event: agent_thinking\ndata: {"secret":"nope"}',
      'event: gate_ready\ndata: {"gate":"facts_review"}',
    ].join("\n\n");

    const frames = parseChunk(chunk);
    expect(frames.map((f) => f.event)).toEqual(["status", "gate_ready"]);
  });
});
