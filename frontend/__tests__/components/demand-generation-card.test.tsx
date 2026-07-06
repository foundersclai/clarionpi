import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { DemandGenerationCard } from "@/components/demand-generation-card";

/**
 * Build a `Response` whose body is an SSE stream (`event:`/`data:` blocks). The
 * `@microsoft/fetch-event-source` transport reads the body via `getReader()` and requires the
 * `text/event-stream` content-type, so this mirrors a real streaming response for the runners.
 */
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

describe("DemandGenerationCard", () => {
  it("accumulates per-section previews in arrival order (rendered text; nothing token-shaped)", async () => {
    const user = userEvent.setup();
    const { container } = renderWithQuery(<DemandGenerationCard matterId="m1" />);

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { phase: "demand", state: "started" } },
        { event: "status", data: { phase: "demand", state: "step", step: "memo" } },
        { event: "section", data: { section_id: "liability", rendered_preview: "Fault is clear." } },
        { event: "section", data: { section_id: "damages", rendered_preview: "Specials total $250,000." } },
        { event: "gate_ready", data: { gate: "compliance_review", matter_id: "m1" } },
        { event: "status", data: { phase: "demand", state: "completed" } },
      ]),
    );

    await user.click(screen.getByTestId("generate-demand"));

    await waitFor(() => {
      expect(screen.getAllByTestId("section-preview")).toHaveLength(2);
    });
    const previews = screen.getAllByTestId("section-preview");
    // Arrival order preserved: liability then damages.
    expect(previews[0]).toHaveAttribute("data-section-id", "liability");
    expect(previews[1]).toHaveAttribute("data-section-id", "damages");
    expect(previews[0]).toHaveTextContent("Fault is clear.");
    expect(previews[1]).toHaveTextContent("Specials total $250,000.");
    // Nothing token-shaped rendered.
    expect(container.innerHTML).not.toContain("[[");
  });

  it("renders a per-section validation-error frame's violations, and the run continues", async () => {
    const user = userEvent.setup();
    const onGateReady = vi.fn();
    renderWithQuery(<DemandGenerationCard matterId="m1" onGateReady={onGateReady} />);

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { state: "started" } },
        {
          event: "error",
          data: {
            phase: "demand",
            error: "section_validation_failed",
            section_id: "liability",
            violations: ["over max_words", "missing required token FACT_1"],
          },
        },
        // The run CONTINUES: a later section still streams.
        { event: "section", data: { section_id: "damages", rendered_preview: "Specials total." } },
        { event: "gate_ready", data: { gate: "compliance_review" } },
        { event: "status", data: { state: "completed" } },
      ]),
    );

    await user.click(screen.getByTestId("generate-demand"));

    const failure = await screen.findByTestId("section-failure");
    expect(failure).toHaveAttribute("data-section-id", "liability");
    expect(within(failure).getByText(/over max_words/i)).toBeInTheDocument();
    expect(within(failure).getByText(/missing required token FACT_1/i)).toBeInTheDocument();
    // The continued section still rendered.
    await waitFor(() => expect(screen.getByTestId("section-preview")).toHaveTextContent("Specials total."));
    // gate_ready still surfaced.
    await waitFor(() => expect(onGateReady).toHaveBeenCalledWith("compliance_review"));
  });

  it("shows the draft_incomplete terminal state with failed sections + a Regenerate; NO advance", async () => {
    const user = userEvent.setup();
    const onGateReady = vi.fn();
    renderWithQuery(<DemandGenerationCard matterId="m1" onGateReady={onGateReady} />);

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { state: "started" } },
        {
          event: "status",
          data: { phase: "demand", state: "draft_incomplete", failed_sections: ["liability"] },
        },
        { event: "status", data: { state: "completed", gate_advanced: false } },
      ]),
    );

    await user.click(screen.getByTestId("generate-demand"));

    const incomplete = await screen.findByTestId("draft-incomplete");
    expect(within(incomplete).getByTestId("failed-sections")).toHaveTextContent("liability");
    expect(screen.getByTestId("regenerate-demand")).toBeInTheDocument();
    // No gate_ready frame → the view never advanced.
    expect(onGateReady).not.toHaveBeenCalled();
  });

  it("advances ONLY on a real gate_ready frame (surfaced to the parent)", async () => {
    const user = userEvent.setup();
    const onGateReady = vi.fn();
    renderWithQuery(<DemandGenerationCard matterId="m1" onGateReady={onGateReady} />);

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { state: "started" } },
        { event: "section", data: { section_id: "liability", rendered_preview: "x" } },
        { event: "gate_ready", data: { gate: "compliance_review" } },
      ]),
    );

    await user.click(screen.getByTestId("generate-demand"));
    await waitFor(() => expect(onGateReady).toHaveBeenCalledWith("compliance_review"));
  });

  it("renders an early typed error frame (no_approved_plan) inline", async () => {
    const user = userEvent.setup();
    renderWithQuery(<DemandGenerationCard matterId="m1" />);

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { state: "started" } },
        { event: "error", data: { phase: "demand", error: "no_approved_plan", detail: "approve first" } },
      ]),
    );

    await user.click(screen.getByTestId("generate-demand"));
    const err = await screen.findByTestId("generation-error");
    expect(err).toHaveTextContent(/No approved plan/i);
  });
});
