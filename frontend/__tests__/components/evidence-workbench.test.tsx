import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { EvidenceWorkbench } from "@/components/evidence-workbench";
import type { EvidenceReviewVM, RoleAffordances } from "@/lib/types";

/**
 * The M6 provenance viewer renders a real pdf.js page when opened; pdf.js is not a jsdom render
 * target, so we mock the rendering primitive behind a test seam. The stub echoes the blobUrl/page
 * it was handed so the anchors-mode wiring (blob_url built centrally, correct page) is asserted.
 */
vi.mock("@/components/pdf-page-view", () => ({
  PdfPageView: ({ blobUrl, page }: { blobUrl: string; page: number }) => (
    <div data-testid="pdf-page-view-stub" data-blob-url={blobUrl} data-page={page} />
  ),
}));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Find the JSON body of the fetch call whose URL matches `predicate`. Typed against the raw
 * `.mock.calls` array (a broad tuple list) so it accepts a spy regardless of which overloaded
 * `fetch` signature `mockResolvedValue` / `mockImplementation` inferred.
 */
function bodyOfCall(
  calls: readonly unknown[][],
  predicate: (url: string) => boolean,
): Record<string, unknown> {
  const call = calls.find((c) => predicate(String(c[0])));
  if (!call) throw new Error("no matching fetch call");
  return JSON.parse((call[1] as RequestInit)?.body as string);
}

const AFFORDANCES_CLEAR: RoleAffordances = {
  can_edit: true,
  can_approve: true,
  approve_blockers: [],
};

function makeVm(overrides: Partial<EvidenceReviewVM> = {}): EvidenceReviewVM {
  return {
    chronology: {
      rows: [
        {
          row_id: "enc-1",
          date_of_service: "2025-02-01",
          provider_display: "Dr. Ramos",
          facility_display: "Valley Ortho",
          encounter_type: "office_visit",
          narrative: "Follow-up for lumbar strain; ROM improving.",
          anchors: [{ document_id: "doc-1", page: 3 }],
          overlay_status: null,
        },
        {
          row_id: "enc-2",
          date_of_service: "2025-03-15",
          provider_display: "Dr. Okafor",
          facility_display: "Mercy Imaging",
          encounter_type: "imaging",
          narrative: "MRI lumbar spine.",
          anchors: [],
          overlay_status: "conflict",
        },
      ],
      conflicts: 1,
      parked: 0,
    },
    ledger: {
      by_category: {
        imaging: {
          billed_cents: 250000,
          adjusted_cents: 50000,
          paid_cents: 100000,
          outstanding_cents: 100000,
        },
        ortho: {
          billed_cents: 400000,
          adjusted_cents: 0,
          paid_cents: 0,
          outstanding_cents: 400000,
        },
      },
      grand_total: {
        billed_cents: 650000,
        adjusted_cents: 50000,
        paid_cents: 100000,
        outstanding_cents: 500000,
      },
      demand_basis_total_cents: 650000,
      basis: "billed",
      line_set_hash: "abc123",
      missing_paid_line_ids: ["line-9"],
      excluded_line_ids: [],
    },
    risk_flags: [
      {
        id: "flag-low",
        kind: "treatment_gap",
        severity: "low",
        detail: "21-day gap between PT visits.",
        anchors: [{ document_id: "doc-2", page: 1 }],
        disposition: null,
        disposition_role: null,
        detector: "date_math",
      },
      {
        id: "flag-high",
        kind: "preexisting_condition",
        severity: "high",
        detail: "Prior lumbar degeneration noted on intake.",
        anchors: [{ document_id: "doc-3", page: 2 }],
        disposition: null,
        disposition_role: null,
        detector: "label",
      },
    ],
    exhibits: {
      entries: [
        {
          exhibit_token_id: "EX_1",
          document_id: "doc-1",
          filename: "ortho_records.pdf",
          included_pages: [1, 2, 3, 4],
          excluded_pages: [],
          phi_disposition: "pending",
          sort_order: 0,
          page_count: 8,
          integrity: "ok",
        },
      ],
      blocking: [],
    },
    dedup_pending: 0,
    ...overrides,
  };
}

function renderWorkbench(vm: EvidenceReviewVM = makeVm(), payloadVersion = 5) {
  return renderWithQuery(
    <EvidenceWorkbench
      matterId="m1"
      vm={vm}
      payloadVersion={payloadVersion}
      roleAffordances={AFFORDANCES_CLEAR}
      analysisRunning={false}
    />,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------------------
// Chronology
// ---------------------------------------------------------------------------------------

describe("EvidenceWorkbench — chronology", () => {
  it("renders rows and a conflict badge with a review hint (no auto-resolve copy)", () => {
    renderWorkbench();
    const rows = screen.getAllByTestId("chronology-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Dr. Ramos")).toBeInTheDocument();
    // Conflict row shows the review badge + hint.
    expect(screen.getByTestId("overlay-conflict")).toHaveTextContent(/conflict/i);
    expect(screen.getByTestId("conflict-hint")).toHaveTextContent(/review both values/i);
    expect(screen.getByTestId("conflict-hint")).not.toHaveTextContent(/resolved|auto/i);
  });

  it("overlay save sends EXACTLY the four closed-vocabulary keys via PUT to the overlay route", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { row_id: "enc-1", overlay_status: "applied" }));

    renderWorkbench();
    // Open the first row's editor, tweak the provider, save.
    const rows = screen.getAllByTestId("chronology-row");
    await user.click(within(rows[0]).getByTestId("chronology-edit"));
    const provider = screen.getByLabelText("Provider");
    await user.clear(provider);
    await user.type(provider, "Dr. Ramos, MD");
    await user.click(screen.getByTestId("overlay-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = bodyOfCall(fetchMock.mock.calls, (u) => u.includes("/chronology/enc-1/overlay"));
    expect(Object.keys(body)).toEqual(["edited_fields"]);
    expect(Object.keys(body.edited_fields as object).sort()).toEqual(
      ["encounter_type", "facility_display", "narrative_override", "provider_display"].sort(),
    );
    expect((body.edited_fields as Record<string, string>).provider_display).toBe("Dr. Ramos, MD");
  });
});

// ---------------------------------------------------------------------------------------
// M6 provenance — view-source (anchors mode)
// ---------------------------------------------------------------------------------------

describe("EvidenceWorkbench — view source (anchors mode)", () => {
  it("a chronology row with anchors opens the viewer in anchors mode (no provenance fetch) with a central blob_url", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderWorkbench();

    // enc-1 carries an anchor (doc-1, page 3); enc-2 carries none → only enc-1 shows the affordance.
    const viewButtons = screen.getAllByTestId("chronology-view-source");
    expect(viewButtons).toHaveLength(1);
    await user.click(viewButtons[0]);

    expect(await screen.findByTestId("provenance-viewer")).toBeInTheDocument();
    // Anchors mode → no provenance hop; the page view gets the CENTRALLY-built blob route.
    const stub = await screen.findByTestId("pdf-page-view-stub");
    expect(stub).toHaveAttribute("data-blob-url", "/api/documents/doc-1/blob");
    expect(stub).toHaveAttribute("data-page", "3");
    expect(screen.getByTestId("provenance-display-form")).toHaveTextContent("Dr. Ramos · 2025-02-01");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a risk flag with anchors opens the viewer in anchors mode", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderWorkbench();

    // Both flags carry an anchor → both expose the affordance.
    const flagButtons = screen.getAllByTestId("flag-view-source");
    expect(flagButtons.length).toBeGreaterThanOrEqual(1);
    await user.click(flagButtons[0]);

    expect(await screen.findByTestId("provenance-viewer")).toBeInTheDocument();
    // The high flag (flag-high) sorts first → its anchor is doc-3, page 2.
    const stub = await screen.findByTestId("pdf-page-view-stub");
    expect(stub).toHaveAttribute("data-blob-url", "/api/documents/doc-3/blob");
    expect(stub).toHaveAttribute("data-page", "2");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------------------
// Ledger
// ---------------------------------------------------------------------------------------

describe("EvidenceWorkbench — ledger", () => {
  it("renders category + grand-total money from cents via centsToDollars", () => {
    renderWorkbench();
    const grand = screen.getByTestId("ledger-grand-total");
    // 650000 cents -> 6,500.00 (from the response fixture — NO client arithmetic).
    expect(within(grand).getByText("6,500.00")).toBeInTheDocument();
    expect(within(grand).getByText("5,000.00")).toBeInTheDocument(); // outstanding
    // Demand basis + gap footnote.
    expect(screen.getByTestId("demand-basis")).toHaveTextContent(/6,500.00/);
    expect(screen.getByTestId("ledger-gaps")).toHaveTextContent(/1 line\(s\) missing a paid amount/);
  });

  it("stages edits into ONE POST with dollar strings, then replaces the ledger from the response", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/billing/lines")) {
        return Promise.resolve(
          jsonResponse(200, {
            lines: [
              {
                id: "line-1",
                provider: "Valley Ortho",
                date_of_service: "2025-02-01",
                service_end_date: null,
                code: "99213",
                billed_cents: 400000,
                adjusted_cents: 0,
                paid_cents: 0,
                outstanding_cents: 400000,
                category: "ortho",
                document_id: "doc-1",
              },
            ],
          }),
        );
      }
      if (url.includes("/billing/edits")) {
        return Promise.resolve(
          jsonResponse(200, {
            outcome: { edited: 1, recategorized: 0, reparsed_money_fields: 1 },
            ledger: {
              by_category: {
                ortho: {
                  billed_cents: 450000,
                  adjusted_cents: 0,
                  paid_cents: 0,
                  outstanding_cents: 450000,
                },
              },
              grand_total: {
                billed_cents: 450000,
                adjusted_cents: 0,
                paid_cents: 0,
                outstanding_cents: 450000,
              },
              demand_basis_total_cents: 450000,
              basis: "billed",
              line_set_hash: "def456",
              missing_paid_line_ids: [],
              excluded_line_ids: [],
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse(200, {}));
    });

    renderWorkbench();
    await user.click(screen.getByTestId("ledger-edit-toggle"));
    // The lines grid loads.
    const billedInput = await screen.findByTestId("billing-billed");
    await user.clear(billedInput);
    await user.type(billedInput, "4,500.00");
    await user.click(screen.getByTestId("billing-save"));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/billing/edits"))).toBe(true),
    );
    // ONE batch POST, money as a dollar STRING.
    const body = bodyOfCall(fetchMock.mock.calls, (u) => u.includes("/billing/edits"));
    expect(Array.isArray(body.edits)).toBe(true);
    expect((body.edits as unknown[]).length).toBe(1);
    const edit = (body.edits as Record<string, unknown>[])[0];
    expect(edit.billing_line_id).toBe("line-1");
    expect(edit.billed).toBe("4,500.00"); // dollar string, not cents

    // The displayed grand total is REPLACED from the server response (4,500.00) — no client sum.
    // (The old 6,500.00 total is gone; the new total comes verbatim from the response fixture.)
    await waitFor(() =>
      expect(
        within(screen.getByTestId("ledger-grand-total")).getAllByText("4,500.00").length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByText("6,500.00")).not.toBeInTheDocument();
  });

  it("renders a per-field 422 invalid_money_string inline", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/billing/lines")) {
        return Promise.resolve(
          jsonResponse(200, {
            lines: [
              {
                id: "line-1",
                provider: "Valley Ortho",
                date_of_service: "2025-02-01",
                service_end_date: null,
                code: null,
                billed_cents: 400000,
                adjusted_cents: null,
                paid_cents: null,
                outstanding_cents: null,
                category: "ortho",
                document_id: "doc-1",
              },
            ],
          }),
        );
      }
      if (url.includes("/billing/edits")) {
        return Promise.resolve(
          jsonResponse(422, { error: "invalid_money_string", detail: "could not parse 'zzz'" }),
        );
      }
      return Promise.resolve(jsonResponse(200, {}));
    });

    renderWorkbench();
    await user.click(screen.getByTestId("ledger-edit-toggle"));
    const paidInput = await screen.findByTestId("billing-paid");
    // A valid value so the client-side parse passes and the request actually fires the 422.
    await user.type(paidInput, "100.00");
    await user.click(screen.getByTestId("billing-save"));

    expect(await screen.findByTestId("billing-submit-error")).toHaveTextContent(
      /not valid dollar values/i,
    );
  });

  it("blocks the batch client-side when a money field is unparseable (no request)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/billing/lines")) {
        return Promise.resolve(
          jsonResponse(200, {
            lines: [
              {
                id: "line-1",
                provider: "Valley Ortho",
                date_of_service: "2025-02-01",
                service_end_date: null,
                code: null,
                billed_cents: 400000,
                adjusted_cents: null,
                paid_cents: null,
                outstanding_cents: null,
                category: "ortho",
                document_id: "doc-1",
              },
            ],
          }),
        );
      }
      return Promise.resolve(jsonResponse(200, {}));
    });

    renderWorkbench();
    await user.click(screen.getByTestId("ledger-edit-toggle"));
    const billed = await screen.findByTestId("billing-billed");
    await user.clear(billed);
    await user.type(billed, "not-money");
    await user.click(screen.getByTestId("billing-save"));

    expect(await screen.findByTestId("billing-billed-error")).toHaveTextContent(/valid dollar/i);
    // No POST to /billing/edits fired.
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/billing/edits"))).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------
// Risk flags
// ---------------------------------------------------------------------------------------

describe("EvidenceWorkbench — risk flags", () => {
  it("groups high-severity first and marks attorney sign-off", () => {
    renderWorkbench();
    const rows = screen.getAllByTestId("risk-flag-row");
    // High sorts before low.
    expect(rows[0]).toHaveAttribute("data-severity", "high");
    expect(within(rows[0]).getByTestId("signoff-required")).toHaveTextContent(/sign-off/i);
    expect(within(rows[0]).getByTestId("detector-chip")).toHaveTextContent(/AI label/i);
  });

  it("blocks omit-without-rationale client-side (button not disabled, no request fires)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, {}));

    renderWorkbench();
    const row = screen.getAllByTestId("risk-flag-row")[0];
    // Choose omit, leave rationale blank, save.
    await user.selectOptions(within(row).getByTestId("disposition-select"), "omit_with_rationale");
    const save = within(row).getByTestId("disposition-save");
    expect(save).not.toBeDisabled(); // NOT gray-disabled — client validation, not a gray-out
    await user.click(save);

    expect(within(row).getByTestId("rationale-error")).toHaveTextContent(/rationale is required/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fires a disposition with a rationale via PUT to the flag route", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { id: "flag-high", disposition: "omit_with_rationale" }));

    renderWorkbench();
    const row = screen.getAllByTestId("risk-flag-row")[0]; // the high flag
    await user.selectOptions(within(row).getByTestId("disposition-select"), "omit_with_rationale");
    await user.type(within(row).getByTestId("rationale-input"), "Addressed via §2(f) showing.");
    await user.click(within(row).getByTestId("disposition-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = bodyOfCall(fetchMock.mock.calls, (u) => u.includes("/flags/flag-high/disposition"));
    expect(body.disposition).toBe("omit_with_rationale");
    expect(body.rationale).toBe("Addressed via §2(f) showing.");
  });

  it("renders a typed 403 role_forbidden inline, button stays enabled", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(403, { error: "role_forbidden", required: ["attorney"], actual: "paralegal" }),
    );

    renderWorkbench();
    const row = screen.getAllByTestId("risk-flag-row")[0];
    const save = within(row).getByTestId("disposition-save");
    await user.click(save);

    expect(await within(row).findByTestId("disposition-error")).toHaveTextContent(/paralegal/i);
    expect(save).not.toBeDisabled();
  });

  it("shows an existing disposition, still re-editable", () => {
    const vm = makeVm({
      risk_flags: [
        {
          id: "flag-x",
          kind: "prior_claim",
          severity: "medium",
          detail: "Prior MVA claim in 2022.",
          anchors: [],
          disposition: "address_in_letter",
          disposition_role: "attorney",
          detector: "label",
          disposition_rationale: "Distinguished on the facts.",
        },
      ],
    });
    renderWorkbench(vm);
    const row = screen.getByTestId("risk-flag-row");
    expect(within(row).getByTestId("current-disposition")).toHaveTextContent(/by attorney/i);
    // The select is still present (re-editable).
    expect(within(row).getByTestId("disposition-select")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------
// Exhibits
// ---------------------------------------------------------------------------------------

describe("EvidenceWorkbench — exhibits", () => {
  it("renders a bare token id (no bracket shape) and the PHI chip", () => {
    renderWorkbench();
    const token = screen.getByTestId("exhibit-token");
    expect(token).toHaveTextContent("EX_1");
    expect(token.textContent).not.toMatch(/\[\[/);
    expect(screen.getByTestId("phi-chip")).toHaveTextContent(/pending/i);
  });

  it("sends the pick payload shape (page lists parsed from range strings + sort order)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        jsonResponse(200, {
          id: "ex-1",
          document_id: "doc-1",
          include_pages: [1, 2, 3, 5],
          excluded_pages: [],
          phi_disposition: "pending",
          sort_order: 2,
        }),
      );

    renderWorkbench();
    const include = screen.getByTestId("include-pages");
    await user.clear(include);
    await user.type(include, "1-3,5");
    const order = screen.getByTestId("sort-order");
    await user.clear(order);
    await user.type(order, "2");
    await user.click(screen.getByTestId("exhibit-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = bodyOfCall(fetchMock.mock.calls, (u) => u.endsWith("/matters/m1/exhibits"));
    expect(body).toEqual({
      document_id: "doc-1",
      include_pages: [1, 2, 3, 5],
      excluded_pages: [],
      sort_order: 2,
    });
  });

  it("blocks an out-of-range page selection inline (no request)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, {}));

    renderWorkbench(); // page_count is 8
    const include = screen.getByTestId("include-pages");
    await user.clear(include);
    await user.type(include, "1-99");
    await user.click(screen.getByTestId("exhibit-save"));

    expect(screen.getByTestId("range-error")).toHaveTextContent(/outside 1–8/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders the blocking banner when the manifest blocks the binder", () => {
    const vm = makeVm({
      exhibits: {
        entries: makeVm().exhibits.entries,
        blocking: ["exhibit doc-1 has open third-party PHI"],
      },
    });
    renderWorkbench(vm);
    expect(screen.getByTestId("exhibits-blocking")).toHaveTextContent(/open third-party PHI/i);
  });

  it("renders a typed PHI 403 inline after learning the exhibit id from a pick", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/matters/m1/exhibits")) {
        return Promise.resolve(
          jsonResponse(200, {
            id: "ex-1",
            document_id: "doc-1",
            include_pages: [1, 2, 3, 4],
            excluded_pages: [],
            phi_disposition: "pending",
            sort_order: 0,
          }),
        );
      }
      if (url.includes("/exhibits/ex-1/phi")) {
        return Promise.resolve(
          jsonResponse(403, { error: "role_forbidden", required: ["attorney"], actual: "paralegal" }),
        );
      }
      return Promise.resolve(jsonResponse(200, {}));
    });

    renderWorkbench();
    // First save the exhibit → learns the exhibit id (ex-1) from the pick response.
    await user.click(screen.getByTestId("exhibit-save"));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/matters/m1/exhibits"))).toBe(
        true,
      ),
    );
    // Now Clear PHI targets ex-1 and gets the typed 403.
    await user.click(screen.getByTestId("phi-clear"));
    expect(await screen.findByTestId("phi-error")).toHaveTextContent(/paralegal/i);
  });

  it("mints tokens on demand and shows the returned bare token ids", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, {
        matter_id: "m1",
        entries: [
          {
            exhibit_token_id: "EX_1",
            document_id: "doc-1",
            filename: "ortho_records.pdf",
            included_pages: [1, 2, 3, 4],
            excluded_pages: [],
            phi_disposition: "pending",
            sort_order: 0,
            page_count: 8,
            integrity: "ok",
          },
        ],
        blocking: [],
      }),
    );

    // Start from a VM with no token yet, then mint.
    const vm = makeVm({
      exhibits: {
        entries: [{ ...makeVm().exhibits.entries[0], exhibit_token_id: null }],
        blocking: [],
      },
    });
    renderWorkbench(vm);
    expect(screen.queryByTestId("exhibit-token")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("mint-tokens"));
    expect(await screen.findByTestId("exhibit-token")).toHaveTextContent("EX_1");
  });
});

// ---------------------------------------------------------------------------------------
// Confirm bar
// ---------------------------------------------------------------------------------------

describe("EvidenceWorkbench — confirm", () => {
  it("approve fires; a 409 override_required opens the dialog and the override resubmit carries the reason", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      // First the approve → 409 override_required. Then the override resubmit → 200.
      .mockResolvedValueOnce(
        jsonResponse(409, {
          error: "override_required",
          guard: "high_flags_open",
          code: "high_severity_flag_open",
          detail: "a high-severity risk flag is still open",
        }),
      )
      .mockResolvedValue(
        jsonResponse(200, {
          result: { transitioned: true, from_state: "evidence_review", to_state: "plan_review" },
          matter: {},
          record_id: "r1",
        }),
      );

    renderWorkbench();
    const confirm = screen.getByTestId("confirm-evidence");
    await user.click(confirm);

    // The dialog opens on the 409.
    const dialog = await screen.findByTestId("override-dialog");
    expect(confirm).not.toBeDisabled(); // never gray-disabled
    // Fill the reason and resubmit.
    await user.type(within(dialog).getByTestId("override-reason"), "Attorney reviewed; proceeding.");
    await user.click(within(dialog).getByTestId("override-submit"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    // The first submit was a plain approve; the second carried the override + reason.
    const first = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit)?.body as string);
    expect(first.action).toBe("approve");
    const second = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit)?.body as string);
    expect(second.action).toBe("override");
    expect(second.override_reason).toBe("Attorney reviewed; proceeding.");
  });

  it("blocks a blank override reason client-side (no resubmit)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse(409, {
          error: "override_required",
          guard: "high_flags_open",
          code: "high_severity_flag_open",
          detail: "a high-severity risk flag is still open",
        }),
      );

    renderWorkbench();
    await user.click(screen.getByTestId("confirm-evidence"));
    const dialog = await screen.findByTestId("override-dialog");
    await user.click(within(dialog).getByTestId("override-submit"));

    expect(within(dialog).getByTestId("override-error")).toHaveTextContent(/reason is required/i);
    // Only the initial approve fired — no resubmit.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders a dedup advisory chip when dedup_pending > 0", () => {
    renderWorkbench(makeVm({ dedup_pending: 3 }));
    expect(screen.getByTestId("dedup-advisory")).toHaveTextContent(/3 duplicate decision/i);
  });
});

// ---------------------------------------------------------------------------------------
// Analysis banner + token sweep
// ---------------------------------------------------------------------------------------

describe("EvidenceWorkbench — analysis banner", () => {
  it("shows the run button and step chips at analysis_running", () => {
    renderWithQuery(
      <EvidenceWorkbench
        matterId="m1"
        vm={makeVm()}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
        analysisRunning
      />,
    );
    expect(screen.getByTestId("run-analysis")).toHaveTextContent(/run analysis/i);
    // The derived panels are NOT shown while parked at analysis_running.
    expect(screen.queryByTestId("chronology-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ledger-panel")).not.toBeInTheDocument();
  });

  it("at evidence_review the button reads Re-run and the panels render", () => {
    renderWorkbench();
    expect(screen.getByTestId("run-analysis")).toHaveTextContent(/re-run/i);
    expect(screen.getByTestId("chronology-panel")).toBeInTheDocument();
    expect(screen.getByTestId("ledger-panel")).toBeInTheDocument();
  });
});

describe("EvidenceWorkbench — no token-shaped strings render anywhere", () => {
  it("sweeps the rendered container for a '[[' token shape", () => {
    const { container } = renderWorkbench(
      makeVm({
        exhibits: {
          entries: [
            { ...makeVm().exhibits.entries[0], exhibit_token_id: "EX_1" },
            {
              exhibit_token_id: "EX_2",
              document_id: "doc-2",
              filename: "imaging.pdf",
              included_pages: [1],
              excluded_pages: [],
              phi_disposition: "cleared",
              sort_order: 1,
              page_count: 2,
              integrity: "ok",
            },
          ],
          blocking: [],
        },
      }),
    );
    // Nothing token-shaped: no "[[" anywhere in the rendered text.
    expect(container.textContent ?? "").not.toMatch(/\[\[/);
  });
});
