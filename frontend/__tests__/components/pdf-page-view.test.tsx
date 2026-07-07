import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";

/**
 * react-pdf renders a real pdf.js document to canvas and spins up a Worker — neither works in
 * jsdom. We mock the library behind a test seam so PdfPageView's OWN logic (highlight frame, the
 * cited banner, page clamping, prev/next, the typed 404 → "source document unavailable") is tested
 * without a real render target or the worker. The stub reads a convention on the `file` prop:
 * `file="404"` simulates a load error carrying `status: 404`; otherwise it reports a page count
 * encoded as `pages:<n>` (default 5), then renders whatever `pageNumber` the `Page` child asks for.
 */
vi.mock("react-pdf", () => {
  const React = require("react");
  return {
    pdfjs: { GlobalWorkerOptions: {} },
    Document: ({
      file,
      onLoadSuccess,
      onLoadError,
      children,
    }: {
      file: string;
      onLoadSuccess?: (info: { numPages: number }) => void;
      onLoadError?: (error: unknown) => void;
      children?: React.ReactNode;
      loading?: React.ReactNode;
    }) => {
      // Depend ONLY on `file` — the parent passes fresh onLoad* closures each render, so keying the
      // effect on them would loop. Latest-ref the callbacks so we always fire the current one. The
      // parent gates the <Page> child by its OWN state (ready), so we render children unconditionally.
      const successRef = React.useRef(onLoadSuccess);
      const errorRef = React.useRef(onLoadError);
      successRef.current = onLoadSuccess;
      errorRef.current = onLoadError;
      React.useEffect(() => {
        if (file === "404") {
          errorRef.current?.({ status: 404 });
          return;
        }
        const m = /pages:(\d+)/.exec(file);
        const numPages = m ? Number(m[1]) : 5;
        successRef.current?.({ numPages });
      }, [file]);
      return <div data-testid="doc-stub">{children}</div>;
    },
    Page: ({ pageNumber }: { pageNumber: number }) => (
      <div data-testid="page-stub" data-page-number={pageNumber} />
    ),
  };
});

// Import AFTER the mock is registered.
import { PdfPageView } from "@/components/pdf-page-view";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PdfPageView — highlight + banner", () => {
  it("shows the cited banner and an amber-ringed frame when highlight is on and on the cited page", async () => {
    renderWithQuery(<PdfPageView blobUrl="pages:10" page={3} pageCount={10} highlight />);
    expect(await screen.findByTestId("cited-banner")).toHaveTextContent("Cited: page 3 of 10");
    // The requested page renders once the document reports ready.
    expect(await screen.findByTestId("page-stub")).toHaveAttribute("data-page-number", "3");
    expect(screen.getByTestId("pdf-frame")).toHaveAttribute("data-highlighted", "true");
    expect(screen.getByTestId("pdf-highlight-overlay")).toBeInTheDocument();
  });

  it("omits the highlight (banner/overlay) when highlight is off", async () => {
    renderWithQuery(<PdfPageView blobUrl="pages:10" page={3} pageCount={10} highlight={false} />);
    await screen.findByTestId("page-stub");
    expect(screen.queryByTestId("cited-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pdf-highlight-overlay")).not.toBeInTheDocument();
    expect(screen.getByTestId("pdf-frame")).toHaveAttribute("data-highlighted", "false");
  });
});

describe("PdfPageView — paging (clamped)", () => {
  it("prev is disabled on page 1; next advances and drops the highlight off the cited page", async () => {
    const user = userEvent.setup();
    renderWithQuery(<PdfPageView blobUrl="pages:3" page={1} pageCount={3} highlight />);
    await screen.findByTestId("page-stub");

    expect(screen.getByTestId("pdf-prev")).toBeDisabled();
    expect(screen.getByTestId("pdf-page-indicator")).toHaveTextContent("Page 1 of 3");
    // On the cited page (1) → highlighted.
    expect(screen.getByTestId("pdf-frame")).toHaveAttribute("data-highlighted", "true");

    await user.click(screen.getByTestId("pdf-next"));
    expect(screen.getByTestId("page-stub")).toHaveAttribute("data-page-number", "2");
    // Off the cited page → highlight suppressed (but the banner still names the cited page).
    expect(screen.getByTestId("pdf-frame")).toHaveAttribute("data-highlighted", "false");
    expect(screen.getByTestId("cited-banner")).toHaveTextContent("Cited: page 1 of 3");
  });

  it("next is disabled on the last page and never exceeds the page count", async () => {
    const user = userEvent.setup();
    renderWithQuery(<PdfPageView blobUrl="pages:2" page={2} pageCount={2} highlight />);
    await screen.findByTestId("page-stub");

    expect(screen.getByTestId("pdf-page-indicator")).toHaveTextContent("Page 2 of 2");
    expect(screen.getByTestId("pdf-next")).toBeDisabled();
  });

  it("clamps a cited page that exceeds the loaded page count", async () => {
    // Anchor claims page 9, but the document only has 3 pages → clamp to 3.
    renderWithQuery(<PdfPageView blobUrl="pages:3" page={9} pageCount={9} highlight />);
    const page = await screen.findByTestId("page-stub");
    expect(page).toHaveAttribute("data-page-number", "3");
    expect(screen.getByTestId("cited-banner")).toHaveTextContent("Cited: page 3 of 3");
  });
});

describe("PdfPageView — load error", () => {
  it("renders 'source document unavailable' on a 404 blob", async () => {
    renderWithQuery(<PdfPageView blobUrl="404" page={1} pageCount={0} highlight />);
    expect(await screen.findByTestId("pdf-error")).toHaveTextContent(/source document unavailable/i);
    expect(screen.queryByTestId("page-stub")).not.toBeInTheDocument();
  });
});
