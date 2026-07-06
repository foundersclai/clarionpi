import { afterEach, describe, expect, it, vi } from "vitest";
import {
  emitPlan,
  findingAction,
  getArtifacts,
  runDemandGeneration,
  runPackageBuild,
} from "@/lib/drafting";
import type { SseFrame } from "@/lib/sse";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function sseResponse(frames: { event: string; data: unknown }[]): Response {
  const text = frames
    .map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`)
    .join("");
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("emitPlan", () => {
  it("POSTs to the plan-emit endpoint (no body)", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { plan: { id: "p1" } }));
    await emitPlan("m1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters/m1/plan/emit");
    expect(init?.method).toBe("POST");
  });
});

describe("findingAction", () => {
  it("POSTs a CLOSED override body to the finding-action endpoint", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { finding: {}, open_blocking: 0 }));

    await findingAction("f1", { action: "override", override_reason: "advisory only" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/findings/f1/action");
    const body = JSON.parse(init?.body as string);
    // Exactly the closed keys — no view-model echo.
    expect(Object.keys(body).sort()).toEqual(["action", "override_reason"].sort());
    expect(body).toEqual({ action: "override", override_reason: "advisory only" });
  });

  it("POSTs a bare patch body (no reason)", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { finding: {}, open_blocking: 0 }));
    await findingAction("f1", { action: "patch" });
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).toEqual({ action: "patch" });
  });
});

describe("getArtifacts", () => {
  it("GETs the artifacts endpoint", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { sets: [] }));
    await getArtifacts("m1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters/m1/artifacts");
    expect(init?.method).toBe("GET");
  });
});

describe("SSE runners deliver in-vocabulary frames in order", () => {
  it("runDemandGeneration streams section + gate_ready frames in arrival order", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { state: "started" } },
        { event: "section", data: { section_id: "a", rendered_preview: "x" } },
        { event: "gate_ready", data: { gate: "compliance_review" } },
      ]),
    );
    const frames: SseFrame[] = [];
    await runDemandGeneration("m1", { onEvent: (f) => frames.push(f) });
    expect(frames.map((f) => f.event)).toEqual(["status", "section", "gate_ready"]);
  });

  it("runPackageBuild streams artifact_ready + gate_ready frames in arrival order", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { state: "started" } },
        { event: "artifact_ready", data: { artifact_kind: "letter_docx", url: "/x" } },
        { event: "gate_ready", data: { gate: "package_ready" } },
      ]),
    );
    const frames: SseFrame[] = [];
    await runPackageBuild("m1", { onEvent: (f) => frames.push(f) });
    expect(frames.map((f) => f.event)).toEqual(["status", "artifact_ready", "gate_ready"]);
  });
});
