import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { StrategyIntakeCard } from "@/components/strategy-intake-card";
import type { RoleAffordances, StrategyIntakeVM } from "@/lib/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeVm(overrides: Partial<StrategyIntakeVM["strategy_inputs"]> = {}): StrategyIntakeVM {
  return {
    strategy_inputs: {
      liability_theory: "",
      injury_framing: "",
      emphasis_notes: "",
      venue_posture: "",
      anchor_amount_cents: null,
      mmi_date: null,
      property_damage_estimate_cents: null,
      ...overrides,
    },
    deadlines_confirmed: true,
  };
}

const AFFORDANCES_CLEAR: RoleAffordances = {
  can_edit: true,
  can_approve: true,
  approve_blockers: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("StrategyIntakeCard", () => {
  it("shows the deadlines-confirmed context line", () => {
    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );
    expect(screen.getByTestId("deadlines-confirmed-context")).toHaveTextContent(
      /Deadlines confirmed at facts review/i,
    );
  });

  it("preserves attorney text VERBATIM (leading/trailing spaces) in the edit payload", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r1" }));

    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    // Type a value with a trailing space (userEvent preserves it in a textarea).
    const liability = screen.getByLabelText("Liability theory");
    await user.type(liability, "rear-ended at a light ");

    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    // Exact text, trailing space intact — no trim on the boundary.
    expect(body.edits.liability_theory).toBe("rear-ended at a light ");
    // Only the changed field is present.
    expect(Object.keys(body.edits)).toEqual(["liability_theory"]);
    // Frozen key set, no overlay echo.
    expect(Object.keys(body).sort()).toEqual(
      ["action", "edits", "idempotency_key", "payload_version"].sort(),
    );
  });

  it("converts a money field to exact integer cents at the wire boundary", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r2" }));

    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    await user.type(screen.getByLabelText("Anchor amount (USD)"), "1,234.56");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body.edits).toEqual({ anchor_amount_cents: 123456 });
  });

  it("approve carries unsaved form edits atomically (the silent-drop regression)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r9" }));

    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    // Fill the form and hit "Submit strategy" WITHOUT saving first — the typed inputs must ride
    // the approve call (dropping them silently discarded the attorney's strategy).
    await user.type(screen.getByLabelText("Liability theory"), "clear rear-end liability");
    await user.type(screen.getByLabelText("Anchor amount (USD)"), "150,000.00");
    await user.click(screen.getByTestId("approve-strategy"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body.action).toBe("approve");
    expect(body.edits).toEqual({
      liability_theory: "clear rear-end liability",
      anchor_amount_cents: 15000000,
    });
  });

  it("approve on an untouched form sends no edits key", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r10" }));

    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    await user.click(screen.getByTestId("approve-strategy"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body.action).toBe("approve");
    expect("edits" in body).toBe(false);
  });

  it("rejects an unparseable money value inline and sends NO request", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, {}));

    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    await user.type(screen.getByLabelText("Anchor amount (USD)"), "abc");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    // Inline error shown; the fetch never fired.
    expect(await screen.findByTestId("anchor_amount-error")).toHaveTextContent(
      /valid dollar amount/i,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears a money field (empty -> null) in the edit payload", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r3" }));

    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm({ anchor_amount_cents: 500000 })}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    const anchor = screen.getByLabelText("Anchor amount (USD)");
    await user.clear(anchor); // now empty -> should clear to null
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body.edits).toEqual({ anchor_amount_cents: null });
  });

  it("approve always fires; a 409 guard_failed budget body renders inline and the button STAYS enabled", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(409, {
        error: "guard_failed",
        guard: "budget_available",
        code: "budget_exhausted",
        detail: "per-matter AI budget is exhausted",
      }),
    );

    renderWithQuery(
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={{
          can_edit: true,
          can_approve: false,
          approve_blockers: [
            {
              guard: "budget_available",
              code: "budget_exhausted",
              detail: "per-matter AI budget is exhausted",
            },
          ],
        }}
      />,
    );

    const approve = screen.getByTestId("approve-strategy");
    await user.click(approve);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const err = await screen.findByTestId("strategy-submit-error");
    expect(err).toHaveTextContent(/budget is exhausted/i);
    expect(approve).not.toBeDisabled();
  });

  it("renders the attorney-required message on a 403 role_forbidden", async () => {
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
      <StrategyIntakeCard
        matterId="m1"
        vm={makeVm()}
        payloadVersion={4}
        roleAffordances={AFFORDANCES_CLEAR}
      />,
    );

    await user.click(screen.getByTestId("approve-strategy"));
    const err = await screen.findByTestId("strategy-submit-error");
    expect(err).toHaveTextContent(/requires an attorney/i);
    expect(err).toHaveTextContent(/paralegal/i);
  });
});
