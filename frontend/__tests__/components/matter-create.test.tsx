import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";

// Mock next/navigation so useRouter().push is a spy (no real router in jsdom).
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import { MatterCreateForm } from "@/components/matter-create-form";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  pushMock.mockReset();
  // localStorage is reset centrally in vitest.setup.ts (beforeEach).
});

describe("MatterCreateForm", () => {
  it("posts the correct payload and routes to the new matter on 201", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        jsonResponse(201, {
          id: "matter-123",
          client_display_name: "Doe, Jane",
          claim_type: "mva",
          jurisdiction: "AZ",
          incident_date: "2025-01-15",
          gate_state: "corpus_processing",
          registry_version: 0,
          deadline_candidates: [],
        }),
      );

    renderWithQuery(<MatterCreateForm />);

    await user.type(screen.getByLabelText("Client display name"), "Doe, Jane");
    await user.type(screen.getByLabelText("Incident date"), "2025-01-15");
    await user.type(screen.getByLabelText("Venue county (optional)"), "Maricopa");
    await user.click(screen.getByRole("button", { name: /create matter/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      client_display_name: "Doe, Jane",
      claim_type: "mva",
      incident_date: "2025-01-15",
      jurisdiction: "AZ",
      venue_county: "Maricopa",
    });

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith("/matters/matter-123"),
    );
  });

  it("renders the jurisdiction_unsupported refusal inline and does not route", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, {
        error: "jurisdiction_unsupported",
        detail: "only AZ supported",
        supported: ["AZ"],
      }),
    );

    renderWithQuery(<MatterCreateForm />);

    await user.type(screen.getByLabelText("Client display name"), "Roe, Rick");
    await user.type(screen.getByLabelText("Incident date"), "2025-02-01");
    await user.click(screen.getByRole("button", { name: /create matter/i }));

    const error = await screen.findByTestId("create-error");
    expect(error).toHaveTextContent(/isn't supported/i);
    expect(error).toHaveTextContent(/AZ/);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("omits venue_county from the payload when left blank", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        jsonResponse(201, {
          id: "m9",
          client_display_name: "No, Venue",
          claim_type: "mva",
          jurisdiction: "AZ",
          incident_date: "2025-03-03",
          gate_state: "corpus_processing",
          registry_version: 0,
          deadline_candidates: [],
        }),
      );

    renderWithQuery(<MatterCreateForm />);
    await user.type(screen.getByLabelText("Client display name"), "No, Venue");
    await user.type(screen.getByLabelText("Incident date"), "2025-03-03");
    await user.click(screen.getByRole("button", { name: /create matter/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).not.toHaveProperty("venue_county");
  });
});
