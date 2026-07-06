import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { FactsReviewCard } from "@/components/facts-review-card";
import type { FactsVM, RoleAffordances } from "@/lib/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VM: FactsVM = {
  deadline_candidates: [
    {
      kind: "sol",
      date: "2027-05-01",
      statute_cite: "A.R.S. § 12-542",
      assumptions: ["discovery rule not applied"],
      verify_status: "unverified",
      confirmed: false,
      rule_id: "A.R.S. § 12-542",
    },
    {
      kind: "notice_of_claim",
      date: "2025-11-01",
      statute_cite: "A.R.S. § 12-821.01",
      assumptions: [],
      verify_status: "verified",
      confirmed: false,
      rule_id: "A.R.S. § 12-821.01",
    },
  ],
  incident_facts: { payload: { venue: "Maricopa" }, anchors: [] },
  documents_summary: { total: 4, needs_review: 1, failed: 0 },
};

const AFFORDANCES_BLOCKED: RoleAffordances = {
  can_edit: true,
  can_approve: false,
  approve_blockers: [
    {
      guard: "deadlines_confirmed",
      code: "deadlines_unconfirmed",
      detail: "SOL / notice-of-claim deadlines are not yet attorney-confirmed",
    },
  ],
};

const AFFORDANCES_CLEAR: RoleAffordances = {
  can_edit: true,
  can_approve: true,
  approve_blockers: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FactsReviewCard", () => {
  it("renders each candidate with kind, date, cite, and verify badge", () => {
    renderWithQuery(
      <FactsReviewCard
        matterId="m1"
        vm={VM}
        payloadVersion={2}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    const rows = screen.getAllByTestId("deadline-row");
    expect(rows).toHaveLength(2);

    const sol = rows[0];
    expect(within(sol).getByText("Statute of limitations")).toBeInTheDocument();
    expect(within(sol).getByText("2027-05-01")).toBeInTheDocument();
    expect(within(sol).getByText("A.R.S. § 12-542")).toBeInTheDocument();
    expect(within(sol).getByText("Pending counsel audit")).toBeInTheDocument();

    const noc = rows[1];
    expect(within(noc).getByText("Counsel-verified")).toBeInTheDocument();

    // Documents summary chips.
    const summary = screen.getByTestId("documents-summary");
    expect(within(summary).getByText("4 document(s)")).toBeInTheDocument();
    expect(within(summary).getByText("1 need review")).toBeInTheDocument();
  });

  it("toggle + save emits EXACTLY {action:'edit', edits:{deadline_confirmations}} with key+version and NO view_model echo", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r1" }));

    renderWithQuery(
      <FactsReviewCard
        matterId="m1"
        vm={VM}
        payloadVersion={2}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    // Toggle the first candidate's Confirmed checkbox on; a staged dot appears.
    const rows = screen.getAllByTestId("deadline-row");
    const firstCheckbox = within(rows[0]).getByRole("checkbox");
    await user.click(firstCheckbox);
    expect(within(rows[0]).getByTestId("staged-indicator")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /save confirmations/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters/m1/gates/facts_review/submit");
    const body = JSON.parse(init?.body as string);

    // Only the changed row is sent.
    expect(body.edits).toEqual({
      deadline_confirmations: [{ rule_id: "A.R.S. § 12-542", confirmed: true }],
    });
    // Frozen key set — no view_model / overlay leakage.
    expect(Object.keys(body).sort()).toEqual(
      ["action", "edits", "idempotency_key", "payload_version"].sort(),
    );
    expect(body.action).toBe("edit");
    expect(body.payload_version).toBe(2);
    expect(body).not.toHaveProperty("view_model");
    expect(body).not.toHaveProperty("role_affordances");
  });

  it("renders the approve_blockers as an advisory list below the button", () => {
    renderWithQuery(
      <FactsReviewCard
        matterId="m1"
        vm={VM}
        payloadVersion={2}
        roleAffordances={AFFORDANCES_BLOCKED}
      />,
    );
    const advisory = screen.getByTestId("approve-blockers");
    expect(
      within(advisory).getByText(/deadlines are not yet attorney-confirmed/i),
    ).toBeInTheDocument();
  });

  it("approve fires the submit even when blockers exist, and a 409 guard_failed body renders inline while the button STAYS enabled", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(409, {
        error: "guard_failed",
        guard: "deadlines_confirmed",
        code: "deadlines_unconfirmed",
        detail: "SOL / notice-of-claim deadlines are not yet attorney-confirmed",
      }),
    );

    renderWithQuery(
      <FactsReviewCard
        matterId="m1"
        vm={VM}
        payloadVersion={2}
        roleAffordances={AFFORDANCES_BLOCKED}
      />,
    );

    const approve = screen.getByTestId("approve-facts");
    await user.click(approve);

    // The request fired (server is the authority — no client-side suppression).
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters/m1/gates/facts_review/submit");

    // The backend detail renders inline, verbatim.
    const err = await screen.findByTestId("facts-submit-error");
    expect(err).toHaveTextContent(/deadlines are not yet attorney-confirmed/i);

    // No gray-out: the approve button is NOT disabled after the refusal.
    expect(approve).not.toBeDisabled();
  });

  it("renders the attorney-required message on a 403 role_forbidden, derived from the typed body", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(403, {
        error: "role_forbidden",
        guard: "role_attorney",
        code: "role_not_attorney",
        detail: "attorney sign-off required; actor role is paralegal (admins do not bypass)",
      }),
    );

    renderWithQuery(
      <FactsReviewCard
        matterId="m1"
        vm={VM}
        payloadVersion={2}
        roleAffordances={AFFORDANCES_BLOCKED}
      />,
    );

    await user.click(screen.getByTestId("approve-facts"));

    const err = await screen.findByTestId("facts-submit-error");
    expect(err).toHaveTextContent(/requires an attorney/i);
    expect(err).toHaveTextContent(/paralegal/i);
  });

  it("triggers an envelope refetch on a stale 409 (gate changed)", async () => {
    const user = userEvent.setup();
    // Submit 409 stale, then the auto-refetch GET.
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse(409, { error: "stale_payload_version", fresh_version: 3 }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          gate: "facts_review",
          payload_version: 3,
          view_model: VM,
          role_affordances: AFFORDANCES_CLEAR,
        }),
      );

    renderWithQuery(
      <FactsReviewCard
        matterId="m1"
        vm={VM}
        payloadVersion={2}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    await user.click(screen.getByTestId("approve-facts"));

    // The refetch hit the current-gate endpoint.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c) => c[0] === "/api/matters/m1/gates/current"),
      ).toBe(true),
    );

    const err = await screen.findByTestId("facts-submit-error");
    expect(err).toHaveTextContent(/gate changed/i);
  });
});
