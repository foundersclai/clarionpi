import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { CompliancePanel } from "@/components/compliance-panel";

// Mock the pdf viewer at the SAME boundary provenance-viewer.test.tsx uses (OTH-01):
// collecting this suite otherwise imports the real chain compliance-panel →
// provenance-viewer → pdf-page-view → react-pdf → pdfjs-dist, whose
// Promise.withResolvers needs Node 22+ — the CI floor is Node 20. These tests verify
// span-to-provenance wiring, not pdf.js rendering (pdf-page-view.test.tsx covers that
// behind its own react-pdf mock). No production polyfill; the seam is test-only.
vi.mock("@/components/pdf-page-view", () => ({
  PdfPageView: ({
    blobUrl,
    page,
    pageCount,
    highlight,
  }: {
    blobUrl: string;
    page: number;
    pageCount: number;
    highlight: boolean;
  }) => (
    <div
      data-testid="pdf-page-view-stub"
      data-blob-url={blobUrl}
      data-page={page}
      data-page-count={pageCount}
      data-highlight={String(highlight)}
    />
  ),
}));
import type {
  ComplianceFindingView,
  ComplianceReviewVM,
  RoleAffordances,
} from "@/lib/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const AFFORDANCES_CLEAR: RoleAffordances = {
  can_edit: true,
  can_approve: true,
  approve_blockers: [],
};

function finding(overrides: Partial<ComplianceFindingView>): ComplianceFindingView {
  return {
    id: "f-1",
    draft_id: "d-1",
    section_id: "liability",
    registry_version: 3,
    check_kind: "tone",
    bucket: "semantic",
    severity: "advisory",
    detail: "Tone reads adversarial.",
    anchors: [],
    span: null,
    status: "open",
    disposition: null,
    override_reason: null,
    ...overrides,
  };
}

function makeVm(overrides: Partial<ComplianceReviewVM> = {}): ComplianceReviewVM {
  return {
    draft: { id: "d-1", version: 1, registry_version: 3, status: "in_compliance", memo: "memo" },
    sections: [
      {
        section_id: "damages",
        sort_order: 1,
        validation: "passed",
        rendered_preview: "Specials total $250,000.",
        spans: [{ span_id: "s1", start: 0, end: 8, token_id: "AMT_1" }],
      },
      {
        section_id: "liability",
        sort_order: 0,
        validation: "passed",
        rendered_preview: "Fault is clear.",
        spans: [],
      },
    ],
    findings: [],
    open_blocking: 0,
    buckets: { mechanical: 0, semantic: 0 },
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CompliancePanel — display", () => {
  it("renders the open-blocking counter + bucket counts", () => {
    renderWithQuery(
      <CompliancePanel
        matterId="m1"
        vm={makeVm({ open_blocking: 2, buckets: { mechanical: 3, semantic: 1 } })}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );
    expect(screen.getByTestId("open-blocking-count")).toHaveTextContent("2 blocking open");
    expect(screen.getByTestId("bucket-mechanical")).toHaveTextContent("3 mechanical");
    expect(screen.getByTestId("bucket-semantic")).toHaveTextContent("1 semantic");
  });

  it("orders the letter preview by sort_order and renders nothing token-shaped", () => {
    const { container } = renderWithQuery(
      <CompliancePanel
        matterId="m1"
        vm={makeVm()}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );
    const sections = screen.getAllByTestId("preview-section");
    // liability (sort_order 0) before damages (sort_order 1).
    expect(sections[0]).toHaveAttribute("data-section-id", "liability");
    expect(sections[1]).toHaveAttribute("data-section-id", "damages");
    // Span data is attached for M6 (bare token id), but no token-shaped copy renders.
    expect(container.querySelector('[data-token-id="AMT_1"]')).not.toBeNull();
    expect(container.innerHTML).not.toContain("[[");
  });

  it("renders findings blocking-first, preserving the wire order", () => {
    // The wire hands them blocking-first; the panel renders in-order.
    const vm = makeVm({
      findings: [
        finding({ id: "blk", check_kind: "orphan_token", bucket: "mechanical", severity: "blocking" }),
        finding({ id: "adv", check_kind: "tone", bucket: "semantic", severity: "advisory" }),
      ],
      open_blocking: 1,
      buckets: { mechanical: 1, semantic: 1 },
    });
    renderWithQuery(
      <CompliancePanel matterId="m1" vm={vm} payloadVersion={5} roleAffordances={AFFORDANCES_CLEAR} />,
    );
    const rows = screen.getAllByTestId("finding-row");
    expect(rows[0]).toHaveAttribute("data-severity", "blocking");
    expect(rows[1]).toHaveAttribute("data-severity", "advisory");
  });
});

describe("CompliancePanel — finding actions", () => {
  it("mechanical+open fires a closed patch body", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { finding: finding({ status: "re_verified" }), open_blocking: 0 }));

    const vm = makeVm({
      findings: [finding({ id: "f-mech", check_kind: "missing_statutory_term", bucket: "mechanical", severity: "blocking" })],
      open_blocking: 1,
      buckets: { mechanical: 1, semantic: 0 },
    });
    renderWithQuery(
      <CompliancePanel matterId="m1" vm={vm} payloadVersion={5} roleAffordances={AFFORDANCES_CLEAR} />,
    );

    await user.click(screen.getByTestId("finding-patch"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/findings/f-mech/action");
    const body = JSON.parse(init?.body as string);
    expect(body).toEqual({ action: "patch" });
  });

  it("semantic override requires a non-blank reason CLIENT-side (no request), then fires the closed body", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { finding: finding({ status: "dispositioned" }), open_blocking: 0 }));

    const vm = makeVm({
      findings: [finding({ id: "f-sem", check_kind: "strategy_drift", bucket: "semantic", severity: "advisory" })],
      buckets: { mechanical: 0, semantic: 1 },
    });
    renderWithQuery(
      <CompliancePanel matterId="m1" vm={vm} payloadVersion={5} roleAffordances={AFFORDANCES_CLEAR} />,
    );

    await user.click(screen.getByTestId("finding-override"));
    // Submit with a blank reason → client-side block, no request.
    await user.click(screen.getByTestId("reason-submit"));
    expect(await screen.findByTestId("finding-local-error")).toHaveTextContent(/reason is required/i);
    expect(fetchMock).not.toHaveBeenCalled();

    // Fill the reason → fires the closed body.
    await user.type(screen.getByTestId("reason-input"), "Advisory; tone is acceptable for this venue.");
    await user.click(screen.getByTestId("reason-submit"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).toEqual({
      action: "override",
      override_reason: "Advisory; tone is acceptable for this venue.",
    });
  });

  it("a hard-block finding shows the explanatory chip and renders a 409 hard_block refusal inline", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(409, { error: "hard_block_not_disposable", check_kind: "orphan_token" }),
    );

    // A hard-block kind that is (contrived) semantic-bucketed, so the accept/override buttons show
    // and the server refusal path can be exercised. The hard-block chip renders regardless of bucket.
    const vm = makeVm({
      findings: [finding({ id: "f-hb", check_kind: "orphan_token", bucket: "semantic", severity: "blocking" })],
      open_blocking: 1,
      buckets: { mechanical: 0, semantic: 1 },
    });
    renderWithQuery(
      <CompliancePanel matterId="m1" vm={vm} payloadVersion={5} roleAffordances={AFFORDANCES_CLEAR} />,
    );

    const row = screen.getByTestId("finding-row");
    expect(within(row).getByTestId("hard-block-chip")).toBeInTheDocument();

    await user.click(within(row).getByTestId("finding-accept"));
    await user.type(screen.getByTestId("reason-input"), "trying to accept");
    await user.click(screen.getByTestId("reason-submit"));

    const err = await screen.findByTestId("finding-server-error");
    expect(err).toHaveTextContent(/hard block/i);
    expect(err).toHaveTextContent(/fixed at the underlying data/i);
  });

  it("a mechanical hard-block finding still offers a patch button", () => {
    const vm = makeVm({
      findings: [finding({ id: "f-hbm", check_kind: "dead_anchor", bucket: "mechanical", severity: "blocking" })],
      open_blocking: 1,
      buckets: { mechanical: 1, semantic: 0 },
    });
    renderWithQuery(
      <CompliancePanel matterId="m1" vm={vm} payloadVersion={5} roleAffordances={AFFORDANCES_CLEAR} />,
    );
    expect(screen.getByTestId("hard-block-chip")).toBeInTheDocument();
    expect(screen.getByTestId("finding-patch")).toBeInTheDocument();
  });
});

describe("CompliancePanel — M6 span click-through", () => {
  it("clicking a mapped letter span opens the provenance viewer in token mode for that token", async () => {
    const user = userEvent.setup();
    // The viewer fetches provenance on open — return an ok body for AMT_1.
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, {
        token_id: "AMT_1",
        display_form: "$250,000",
        outcome: "ok",
        source: "extractor",
        anchors: [],
      }),
    );

    renderWithQuery(
      <CompliancePanel matterId="m1" vm={makeVm()} payloadVersion={5} roleAffordances={AFFORDANCES_CLEAR} />,
    );

    // The damages section's preview carries a span over "Specials" (offsets 0..8) → token AMT_1.
    const span = screen.getAllByTestId("preview-span").find((el) => el.getAttribute("data-token-id") === "AMT_1");
    expect(span).toBeDefined();
    await user.click(span!);

    // The viewer opens and fetches AMT_1's provenance from the pinned route.
    await waitFor(() => expect(screen.getByTestId("provenance-viewer")).toBeInTheDocument());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const provenanceCall = fetchMock.mock.calls.find((c) => String(c[0]).includes("/provenance/"));
    expect(provenanceCall).toBeDefined();
    expect(String(provenanceCall![0])).toBe("/api/matters/m1/provenance/AMT_1");
    // The bare token id renders as an inert label; still nothing token-shaped ("[[") in the DOM.
    expect(await screen.findByTestId("provenance-token-id")).toHaveTextContent("AMT_1");
    expect(document.body.innerHTML).not.toContain("[[");
  });

  it("does not render the viewer until a span is clicked", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, {}));
    renderWithQuery(
      <CompliancePanel matterId="m1" vm={makeVm()} payloadVersion={5} roleAffordances={AFFORDANCES_CLEAR} />,
    );
    expect(screen.queryByTestId("provenance-viewer")).not.toBeInTheDocument();
  });
});

describe("CompliancePanel — G3 approve", () => {
  it("approve is ALWAYS clickable; a no_blocking_findings guard renders inline and the button stays enabled", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(409, {
        error: "guard_failed",
        guard: "compliance",
        code: "no_blocking_findings",
        detail: "2 blocking findings remain",
      }),
    );

    renderWithQuery(
      <CompliancePanel
        matterId="m1"
        vm={makeVm({ open_blocking: 2 })}
        payloadVersion={5}
        roleAffordances={{
          can_edit: true,
          can_approve: false,
          approve_blockers: [
            { guard: "compliance", code: "no_blocking_findings", detail: "2 blocking findings remain" },
          ],
        }}
      />,
    );

    const approve = screen.getByTestId("approve-compliance");
    expect(approve).not.toBeDisabled();
    await user.click(approve);

    const err = await screen.findByTestId("compliance-submit-error");
    expect(err).toHaveTextContent(/2 blocking findings remain/i);
    expect(approve).not.toBeDisabled();
  });
});
