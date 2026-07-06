import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { PlanReviewCard } from "@/components/plan-review-card";
import type { PlanReviewVM, PlanView, RoleAffordances } from "@/lib/types";

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

function makePlan(overrides: Partial<PlanView> = {}): PlanView {
  return {
    id: "plan-1",
    matter_id: "m1",
    version: 1,
    registry_version: 3,
    demand_amount_cents: 25000000,
    demand_type: "open",
    sections: [
      {
        section_id: "liability",
        purpose: "Establish fault",
        allowed_tokens: ["FACT_1", "FACT_2"],
        required_tokens: ["FACT_1"],
        max_words: 300,
      },
      {
        section_id: "damages",
        purpose: "Total the specials",
        allowed_tokens: ["AMT_1"],
        required_tokens: [],
        max_words: 250,
      },
    ],
    emphasis_directives: ["Lead with the rear-end liability."],
    approved: false,
    ...overrides,
  };
}

function makeVm(overrides: Partial<PlanReviewVM> = {}): PlanReviewVM {
  return {
    plan: makePlan(),
    plan_missing: false,
    registry_version_current: 3,
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PlanReviewCard — plan_missing", () => {
  it("shows the build-plan explainer and POSTs the emit on click", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { plan: makePlan() }));

    renderWithQuery(
      <PlanReviewCard
        matterId="m1"
        vm={{ plan: null, plan_missing: true, registry_version_current: 3 }}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    expect(screen.getByTestId("plan-review-card")).toHaveAttribute("data-plan-missing", "true");
    await user.click(screen.getByTestId("build-plan"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters/m1/plan/emit");
    expect(init?.method).toBe("POST");
  });

  it("renders letter_structure_missing inline (422)", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, { error: "letter_structure_missing", detail: "no skeleton" }),
    );

    renderWithQuery(
      <PlanReviewCard
        matterId="m1"
        vm={{ plan: null, plan_missing: true, registry_version_current: 3 }}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    await user.click(screen.getByTestId("build-plan"));
    const err = await screen.findByTestId("plan-emit-error");
    expect(err).toHaveTextContent(/no demand-letter skeleton/i);
  });
});

describe("PlanReviewCard — plan present", () => {
  it("renders the version, the unapproved badge, and BARE token chips (nothing token-shaped)", () => {
    const { container } = renderWithQuery(
      <PlanReviewCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );
    expect(screen.getByTestId("plan-version")).toHaveTextContent("v1");
    expect(screen.getByTestId("unapproved-badge")).toBeInTheDocument();
    // The allowed-token chip shows the bare id.
    expect(screen.getAllByText("FACT_1").length).toBeGreaterThan(0);
    // Nothing token-shaped renders anywhere.
    expect(container.innerHTML).not.toContain("[[");
  });

  it("converts the demand amount to exact cents; sends ONLY the changed field (closed body)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r1" }));

    renderWithQuery(
      <PlanReviewCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    const amount = screen.getByLabelText("Demand amount (USD)");
    await user.clear(amount);
    await user.type(amount, "300,000.00");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters/m1/gates/plan_review/submit");
    const body = JSON.parse(init?.body as string);
    expect(body.action).toBe("edit");
    expect(body.edits).toEqual({ demand_amount_cents: 30000000 });
    // Frozen key set — no view-model echo.
    expect(Object.keys(body).sort()).toEqual(
      ["action", "edits", "idempotency_key", "payload_version"].sort(),
    );
  });

  it("sends a per-section max_words + required-token edit with only the changed section", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r2" }));

    renderWithQuery(
      <PlanReviewCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    // Change the liability section's max_words + add a required token.
    await user.clear(screen.getByLabelText("Max words", { selector: "#max-words-liability" }));
    await user.type(
      screen.getByLabelText("Max words", { selector: "#max-words-liability" }),
      "350",
    );
    const required = screen.getByLabelText(/Required tokens/i, { selector: "#required-liability" });
    await user.clear(required);
    await user.type(required, "FACT_1, FACT_2");

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body.edits.sections).toEqual([
      { section_id: "liability", max_words: 350, required_tokens: ["FACT_1", "FACT_2"] },
    ]);
    // The unchanged `damages` section is not in the payload.
    expect(body.edits.sections).toHaveLength(1);
  });

  it("approve always fires; a strategy_plan/plan_registry_drift refusal renders re-emit copy, button STAYS enabled", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(409, {
        error: "guard_failed",
        guard: "strategy_plan",
        code: "plan_registry_drift",
        detail: "plan pinned to registry 2, matter at 3",
      }),
    );

    renderWithQuery(
      <PlanReviewCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    const approve = screen.getByTestId("approve-plan");
    await user.click(approve);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const err = await screen.findByTestId("plan-submit-error");
    expect(err).toHaveTextContent(/records changed since this plan was drafted/i);
    expect(err).toHaveTextContent(/re-build the plan/i);
    expect(approve).not.toBeDisabled();
  });

  it("rejects an unparseable demand amount inline and sends NO request", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, {}));

    renderWithQuery(
      <PlanReviewCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={5}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    const amount = screen.getByLabelText("Demand amount (USD)");
    await user.clear(amount);
    await user.type(amount, "lots");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(await screen.findByTestId("demand_amount-error")).toHaveTextContent(/valid dollar amount/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
