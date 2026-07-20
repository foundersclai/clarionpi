import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { ProvenanceViewer } from "@/components/provenance-viewer";
import type { AnchorLike, ProvenanceResponse } from "@/lib/provenance";

/**
 * pdf.js is not a real render target in jsdom, so we mock the rendering primitive behind a test
 * seam: the stub echoes its props as data-attributes so the viewer's ANCHOR→page wiring (blobUrl,
 * page, pageCount, highlight) is asserted without touching react-pdf / the worker.
 */
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

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeProvenance(overrides: Partial<ProvenanceResponse> = {}): ProvenanceResponse {
  return {
    token_id: "FACT_3",
    display_form: "cervical strain",
    outcome: "ok",
    source: "extractor",
    anchors: [
      {
        document_id: "doc-1",
        page: 3,
        bbox: null,
        blob_url: "/api/documents/doc-1/blob",
        page_count: 12,
        filename: "02_er_note.pdf",
        doc_type: "medical_record",
        superseded: false,
      },
    ],
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ProvenanceViewer — closed", () => {
  it("renders nothing and fetches nothing when closed", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, makeProvenance()));
    renderWithQuery(
      <ProvenanceViewer
        matterId="m1"
        open={false}
        onClose={() => {}}
        source={{ kind: "token", tokenId: "FACT_3" }}
      />,
    );
    expect(screen.queryByTestId("provenance-viewer")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("ProvenanceViewer — token mode", () => {
  it("fetches provenance ONCE on open (lazy) and renders the display form + ok badge + source chip", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, makeProvenance()));
    renderWithQuery(
      <ProvenanceViewer
        matterId="m1"
        open
        onClose={() => {}}
        source={{ kind: "token", tokenId: "FACT_3" }}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("provenance-display-form")).toHaveTextContent("cervical strain"));
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/matters/m1/provenance/FACT_3");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const outcome = screen.getByTestId("provenance-outcome");
    expect(outcome).toHaveAttribute("data-outcome", "ok");
    expect(outcome).toHaveTextContent(/verified/i);
    expect(screen.getByTestId("provenance-source")).toHaveTextContent("extractor");
    // The bare token id renders as an inert label (never token-shaped copy).
    expect(screen.getByTestId("provenance-token-id")).toHaveTextContent("FACT_3");
    // The anchor row labels the source page by document NAME, not a bare uuid.
    const docLabel = screen.getByTestId("anchor-doc-label");
    expect(docLabel).toHaveTextContent("02_er_note.pdf");
    expect(screen.queryByText("doc-1")).toBeNull();
    expect(screen.getByText(/page 3 of 12 · medical record/)).toBeInTheDocument();
  });

  it("maps each outcome to its badge (unverified→pending, disputed→red, amt_mismatch→ledger drift)", async () => {
    const cases: Array<[ProvenanceResponse["outcome"], RegExp]> = [
      ["unverified", /pending verification/i],
      ["disputed", /disputed/i],
      ["amt_mismatch", /ledger drift/i],
    ];
    for (const [outcome, label] of cases) {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, makeProvenance({ outcome })));
      const { unmount } = renderWithQuery(
        <ProvenanceViewer
          matterId="m1"
          open
          onClose={() => {}}
          source={{ kind: "token", tokenId: "FACT_3" }}
        />,
      );
      const badge = await screen.findByTestId("provenance-outcome");
      expect(badge).toHaveAttribute("data-outcome", outcome);
      expect(badge).toHaveTextContent(label);
      unmount();
      vi.restoreAllMocks();
    }
  });

  it("badges a superseded anchor 'superseded source'", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        200,
        makeProvenance({
          anchors: [
            { document_id: "doc-1", page: 3, bbox: null, blob_url: "/api/documents/doc-1/blob", page_count: 12, superseded: true },
          ],
        }),
      ),
    );
    renderWithQuery(
      <ProvenanceViewer matterId="m1" open onClose={() => {}} source={{ kind: "token", tokenId: "FACT_3" }} />,
    );
    expect(await screen.findByTestId("anchor-superseded")).toHaveTextContent(/superseded source/i);
  });

  it("selecting an anchor renders the page view with the server-sent blob_url + page + count + highlight", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        200,
        makeProvenance({
          anchors: [
            { document_id: "doc-1", page: 3, bbox: null, blob_url: "/api/documents/doc-1/blob", page_count: 12, superseded: false },
            { document_id: "doc-2", page: 7, bbox: null, blob_url: "/api/documents/doc-2/blob", page_count: 20, superseded: false },
          ],
        }),
      ),
    );
    renderWithQuery(
      <ProvenanceViewer matterId="m1" open onClose={() => {}} source={{ kind: "token", tokenId: "FACT_3" }} />,
    );

    // The first anchor is selected by default → its page view mounts with the sent blob_url.
    const stub = await screen.findByTestId("pdf-page-view-stub");
    expect(stub).toHaveAttribute("data-blob-url", "/api/documents/doc-1/blob");
    expect(stub).toHaveAttribute("data-page", "3");
    expect(stub).toHaveAttribute("data-page-count", "12");
    expect(stub).toHaveAttribute("data-highlight", "true");

    // Selecting the second anchor swaps the page view to that anchor's page (still server-sent url).
    const rows = screen.getAllByTestId("anchor-row");
    await user.click(rows[1]);
    await waitFor(() =>
      expect(screen.getByTestId("pdf-page-view-stub")).toHaveAttribute("data-blob-url", "/api/documents/doc-2/blob"),
    );
    expect(screen.getByTestId("pdf-page-view-stub")).toHaveAttribute("data-page", "7");
  });

  it("renders a token refusal verbatim (404 token_not_found)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(404, { error: "token_not_found" }));
    renderWithQuery(
      <ProvenanceViewer matterId="m1" open onClose={() => {}} source={{ kind: "token", tokenId: "NOPE" }} />,
    );
    expect(await screen.findByTestId("provenance-error")).toHaveTextContent("token_not_found");
  });
});

describe("ProvenanceViewer — AMT composition", () => {
  const composition = {
    column: "billed",
    hint: "ER billed",
    lines: [
      {
        line_id: "l1",
        provider: "Saguaro Regional Medical Center",
        date_of_service: "2025-03-14",
        category: "er",
        amount: "$9,200.00",
        anchor: {
          document_id: "doc-1",
          page: 1,
          bbox: null,
          blob_url: "/api/documents/doc-1/blob",
          page_count: 1,
          filename: "03_er_bill.pdf",
          doc_type: "bill",
          superseded: false,
        },
      },
      {
        line_id: "l2",
        provider: "Saguaro Regional Medical Center",
        date_of_service: "2025-03-14",
        category: "er",
        amount: "$1,450.00",
        anchor: {
          document_id: "doc-2",
          page: 1,
          bbox: null,
          blob_url: "/api/documents/doc-2/blob",
          page_count: 1,
          filename: "08_er_bill_resend.pdf",
          doc_type: "bill",
          superseded: false,
        },
      },
    ],
    missing_line_ids: [],
  } as const;

  it("renders the ledger summary + per-line headings and opens a line's own bill page", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        200,
        makeProvenance({
          token_id: "AMT_11",
          display_form: "$21,300.00",
          anchors: [], // a computed sum lives on no page — its provenance is the composition
          composition: JSON.parse(JSON.stringify(composition)),
        }),
      ),
    );
    renderWithQuery(
      <ProvenanceViewer matterId="m1" open onClose={() => {}} source={{ kind: "token", tokenId: "AMT_11" }} />,
    );

    expect(await screen.findByTestId("composition-summary")).toHaveTextContent(
      "Computed from the billing ledger — ER billed · sum of 2 bill lines",
    );
    const headings = screen.getAllByTestId("anchor-heading");
    expect(headings[0]).toHaveTextContent("Saguaro Regional Medical Center · 2025-03-14 · $9,200.00");
    expect(headings[1]).toHaveTextContent("Saguaro Regional Medical Center · 2025-03-14 · $1,450.00");
    // Each row still names its bill document; nothing shows the empty-anchors message.
    expect(screen.getAllByTestId("anchor-doc-label")[0]).toHaveTextContent("03_er_bill.pdf");
    expect(screen.queryByTestId("provenance-no-anchors")).not.toBeInTheDocument();

    // The first line's page renders by default; selecting the second swaps to ITS bill page.
    expect(screen.getByTestId("pdf-page-view-stub")).toHaveAttribute(
      "data-blob-url",
      "/api/documents/doc-1/blob",
    );
    await user.click(screen.getAllByTestId("anchor-row")[1]);
    await waitFor(() =>
      expect(screen.getByTestId("pdf-page-view-stub")).toHaveAttribute(
        "data-blob-url",
        "/api/documents/doc-2/blob",
      ),
    );
  });

  it("surfaces unresolved ledger-ref ids and anchorless lines instead of dropping them", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        200,
        makeProvenance({
          token_id: "AMT_10",
          display_form: "$4.20",
          anchors: [],
          composition: {
            column: "billed",
            hint: "total billed specials",
            lines: [
              {
                line_id: "l3",
                provider: "Desert Pharmacy",
                date_of_service: "2025-03-16",
                category: "pharmacy",
                amount: "$4.20",
                anchor: null,
              },
            ],
            missing_line_ids: ["ghost-1"],
          },
        }),
      ),
    );
    renderWithQuery(
      <ProvenanceViewer matterId="m1" open onClose={() => {}} source={{ kind: "token", tokenId: "AMT_10" }} />,
    );

    expect(await screen.findByTestId("composition-missing")).toHaveTextContent(
      "1 ledger line in this figure no longer resolves.",
    );
    expect(screen.getByTestId("composition-unlinked")).toHaveTextContent(
      "Desert Pharmacy · 2025-03-16 · $4.20 — no source page linked",
    );
  });
});

describe("ProvenanceViewer — anchors mode", () => {
  it("does NOT fetch provenance and builds blob_url via blobUrlFor from bare {document_id, page}", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const anchors: AnchorLike[] = [
      { document_id: "doc-5", page: 2 },
      { document_id: "doc-6", page: 4 },
    ];
    renderWithQuery(
      <ProvenanceViewer
        matterId="m1"
        open
        onClose={() => {}}
        source={{ kind: "anchors", anchors, label: "Dr. Ramos · 2025-02-01" }}
      />,
    );

    expect(screen.getByTestId("provenance-display-form")).toHaveTextContent("Dr. Ramos · 2025-02-01");
    // The default-selected anchor renders with the CENTRALLY-built route (no server blob_url on the wire).
    const stub = await screen.findByTestId("pdf-page-view-stub");
    expect(stub).toHaveAttribute("data-blob-url", "/api/documents/doc-5/blob");
    expect(stub).toHaveAttribute("data-page", "2");
    // No provenance hop in anchors mode.
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("ProvenanceViewer — close behavior", () => {
  it("closes on the close button, the backdrop, and Escape", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, makeProvenance()));

    const onClose = vi.fn();
    renderWithQuery(
      <ProvenanceViewer matterId="m1" open onClose={onClose} source={{ kind: "token", tokenId: "FACT_3" }} />,
    );

    await user.click(screen.getByTestId("provenance-close"));
    expect(onClose).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId("provenance-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(2);

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
