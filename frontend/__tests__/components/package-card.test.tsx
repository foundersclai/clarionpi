import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { PackageCard } from "@/components/package-card";
import type { ArtifactSetView, PackageVM } from "@/lib/types";

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

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const READY_SET: ArtifactSetView = {
  current: true,
  id: "set-1",
  draft_version: 1,
  registry_version: 3,
  created_at: "2026-07-06T12:00:00Z",
  artifacts: [
    {
      kind: "letter_docx",
      sha256: "abcdef0123456789aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      byte_count: 45000,
      url: "/api/matters/m1/artifacts/set-1/letter_docx",
    },
    {
      kind: "binder_pdf",
      sha256: "ffeeddccbbaa99887766554433221100ffeeddccbbaa998877665544332211",
      byte_count: 2_500_000,
      url: "/api/matters/m1/artifacts/set-1/binder_pdf",
    },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PackageCard — package_assembly build", () => {
  it("lights up artifact chips per artifact_ready frame; advances ONLY on gate_ready", async () => {
    const user = userEvent.setup();
    const onGateReady = vi.fn();
    renderWithQuery(
      <PackageCard
        matterId="m1"
        gate="package_assembly"
        vm={{ artifact_sets: [], buildable: true, registry_version_current: true, new_cycle_required: false }}
        onGateReady={onGateReady}
      />,
    );

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { phase: "package", state: "started" } },
        { event: "artifact_ready", data: { artifact_kind: "letter_docx", url: "/x" } },
        { event: "artifact_ready", data: { artifact_kind: "binder_pdf", url: "/x" } },
        { event: "gate_ready", data: { gate: "package_ready", matter_id: "m1" } },
        { event: "status", data: { phase: "package", state: "completed", reused: false } },
      ]),
    );

    await user.click(screen.getByTestId("build-package"));

    await waitFor(() => {
      const progress = screen.getByTestId("build-progress");
      expect(within(progress).getByText(/Demand letter/i).closest("[data-artifact-kind]")).toHaveAttribute(
        "data-done",
        "true",
      );
    });
    const progress = screen.getByTestId("build-progress");
    expect(
      within(progress).getByText(/Exhibit binder/i).closest("[data-artifact-kind]"),
    ).toHaveAttribute("data-done", "true");
    await waitFor(() => expect(onGateReady).toHaveBeenCalledWith("package_ready"));
  });

  it("renders a binder_blocked banner with its reasons and the pending-PHI hint; no advance", async () => {
    const user = userEvent.setup();
    const onGateReady = vi.fn();
    renderWithQuery(
      <PackageCard
        matterId="m1"
        gate="package_assembly"
        vm={{ artifact_sets: [], buildable: true, registry_version_current: true, new_cycle_required: false }}
        onGateReady={onGateReady}
      />,
    );

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { phase: "package", state: "started" } },
        {
          event: "error",
          data: {
            phase: "package",
            error: "binder_blocked",
            reasons: ["exhibit EX_2 has a pending third-party-PHI disposition"],
          },
        },
      ]),
    );

    await user.click(screen.getByTestId("build-package"));

    const banner = await screen.findByTestId("binder-blocked");
    expect(within(banner).getByTestId("binder-blocked-reasons")).toHaveTextContent(
      /pending third-party-PHI disposition/i,
    );
    expect(banner).toHaveTextContent(/third-party-PHI exhibit still pending/i);
    expect(onGateReady).not.toHaveBeenCalled();
  });

  it("renders rule_pack_unaudited as a blocking build error with safe copy; no advance", async () => {
    const user = userEvent.setup();
    const onGateReady = vi.fn();
    renderWithQuery(
      <PackageCard
        matterId="m1"
        gate="package_assembly"
        vm={{ artifact_sets: [], buildable: true, registry_version_current: true, new_cycle_required: false }}
        onGateReady={onGateReady}
      />,
    );

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        { event: "status", data: { phase: "package", state: "started" } },
        {
          event: "error",
          data: {
            phase: "package",
            error: "rule_pack_unaudited",
            jurisdiction: "AZ",
            pack_version: "0.1.0",
          },
        },
      ]),
    );

    await user.click(screen.getByTestId("build-package"));

    // Concise attorney copy; no backend detail, versions, or fingerprints rendered.
    await screen.findByText("Rule pack requires attorney audit before package build.");
    expect(screen.queryByText(/0\.1\.0/)).not.toBeInTheDocument();
    expect(onGateReady).not.toHaveBeenCalled();
  });

  it("renders rule_pack_changed with administrator copy; no advance", async () => {
    const user = userEvent.setup();
    const onGateReady = vi.fn();
    renderWithQuery(
      <PackageCard
        matterId="m1"
        gate="package_assembly"
        vm={{ artifact_sets: [], buildable: true, registry_version_current: true, new_cycle_required: false }}
        onGateReady={onGateReady}
      />,
    );

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        {
          event: "error",
          data: { phase: "package", error: "rule_pack_changed", jurisdiction: "AZ" },
        },
      ]),
    );

    await user.click(screen.getByTestId("build-package"));

    await screen.findByText(/rule pack changed after this matter was created/i);
    expect(onGateReady).not.toHaveBeenCalled();
  });
  it("shows the not-buildable hint only when buildable is false; the build button stays enabled", () => {
    const base = { artifact_sets: [], registry_version_current: true, new_cycle_required: false };
    // buildable=false (backend draft not yet APPROVED): the hint shows, but the build button is
    // NOT disabled — the hint is advisory, never the build gate (which is gate_state).
    const { unmount } = renderWithQuery(
      <PackageCard matterId="m1" gate="package_assembly" vm={{ ...base, buildable: false }} />,
    );
    expect(screen.getByTestId("not-buildable-hint")).toBeInTheDocument();
    expect(screen.getByTestId("build-package")).toBeEnabled();
    unmount();
    // buildable=true (WD-2: the backend feeds True once G3 marks the draft APPROVED) -> hint gone.
    renderWithQuery(
      <PackageCard matterId="m1" gate="package_assembly" vm={{ ...base, buildable: true }} />,
    );
    expect(screen.queryByTestId("not-buildable-hint")).not.toBeInTheDocument();
  });
});

describe("PackageCard — package_ready downloads", () => {
  it("lists artifact sets with exact download hrefs and a SHORT sha", async () => {
    const vm: PackageVM = { artifact_sets: [READY_SET], buildable: false, registry_version_current: true, new_cycle_required: false };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, { sets: [READY_SET] }));

    const { container } = renderWithQuery(
      <PackageCard matterId="m1" gate="package_ready" vm={vm} />,
    );

    const rows = await screen.findAllByTestId("artifact-row");
    expect(rows).toHaveLength(2);

    const links = screen.getAllByTestId("artifact-download");
    // Exact same-origin hrefs, plain anchors (browser-native download).
    expect(links[0]).toHaveAttribute("href", "/api/matters/m1/artifacts/set-1/letter_docx");
    expect(links[1]).toHaveAttribute("href", "/api/matters/m1/artifacts/set-1/binder_pdf");
    expect(links[0].tagName).toBe("A");

    // Sha rendered short (12 chars), not the full 64.
    const shas = screen.getAllByTestId("artifact-sha");
    expect(shas[0]).toHaveTextContent("abcdef012345");
    expect(shas[0].textContent?.length).toBe(12);

    // Immutability note present; nothing token-shaped anywhere.
    expect(screen.getByTestId("immutability-note")).toHaveTextContent(/fresh draft cycle/i);
    expect(container.innerHTML).not.toContain("[[");
  });

  it("falls back to the VM's artifact sets when the query has not resolved", () => {
    const vm: PackageVM = { artifact_sets: [READY_SET], buildable: false, registry_version_current: true, new_cycle_required: false };
    // No fetch resolution needed — the VM already carries the sets.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, { sets: [] }));

    renderWithQuery(<PackageCard matterId="m1" gate="package_ready" vm={vm} />);
    // The VM set renders immediately (before/without the query).
    expect(screen.getAllByTestId("artifact-row").length).toBeGreaterThan(0);
  });
});

describe("PackageCard — new cycle required (BUS-05)", () => {
  it("labels sets historical and posts start_cycle through the gate submit", async () => {
    const user = userEvent.setup();
    const vm: PackageVM = {
      artifact_sets: [READY_SET],
      buildable: false,
      registry_version_current: false,
      new_cycle_required: true,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (method === "GET" && url.includes("/artifacts")) {
          return jsonResponse(200, { sets: [READY_SET] });
        }
        if (method === "GET" && url.includes("/gates/current")) {
          return jsonResponse(200, {
            gate: "package_ready",
            payload_version: 7,
            view_model: vm,
            role_affordances: { can_edit: false, can_approve: false, approve_blockers: [] },
          });
        }
        if (method === "POST" && url.includes("/gates/package_ready/submit")) {
          return jsonResponse(200, {
            result: {
              transitioned: true,
              from_state: "package_ready",
              to_state: "evidence_review",
              replayed: false,
            },
          });
        }
        throw new Error(`unexpected fetch: ${method} ${url}`);
      });

    renderWithQuery(<PackageCard matterId="m1" gate="package_ready" vm={vm} />);

    // The banner + the historical label (never "current" once new records outran the set).
    await screen.findByTestId("new-cycle-required");
    expect(screen.getByTestId("historical-badge")).toBeInTheDocument();
    expect(screen.queryByText(/^current$/)).not.toBeInTheDocument();
    // Downloads stay available.
    expect(screen.getAllByTestId("artifact-download").length).toBeGreaterThan(0);

    // The button posts the typed start_cycle action with the live payload_version fence.
    const button = screen.getByTestId("start-new-cycle");
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    await waitFor(() => {
      const submitCall = fetchMock.mock.calls.find(([url, init]) =>
        String(url).includes("/submit") && init?.method === "POST",
      );
      expect(submitCall).toBeTruthy();
      const body = JSON.parse(String(submitCall![1]?.body));
      expect(body.action).toBe("start_cycle");
      expect(body.payload_version).toBe(7);
      expect(typeof body.idempotency_key).toBe("string");
    });
  });
});
