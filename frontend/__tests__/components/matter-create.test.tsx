import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
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

const INTAKE_QUESTIONS = [
  "Is a public entity involved?",
  "Is the plaintiff a minor?",
  "Is this a wrongful-death claim?",
  "Is there a coverage dispute?",
];

/** Answer every intake question (default all "No" — the in-box matter). */
async function answerIntake(user: UserEvent, answers: Record<string, string> = {}) {
  for (const question of INTAKE_QUESTIONS) {
    const group = screen.getByRole("group", { name: question });
    await user.click(within(group).getByLabelText(answers[question] ?? "No"));
  }
}

const CREATED_MATTER = {
  id: "matter-123",
  client_display_name: "Doe, Jane",
  claim_type: "mva",
  jurisdiction: "AZ",
  incident_date: "2025-01-15",
  gate_state: "corpus_processing",
  registry_version: 0,
  deadline_candidates: [
    {
      kind: "sol",
      date: "2027-01-15",
      statute_cite: "A.R.S. § 12-542",
      assumptions: [],
      verify_status: "unverified",
      confirmed: false,
    },
  ],
  public_entity_involved: "no",
  plaintiff_is_minor: "no",
  wrongful_death: "no",
  coverage_dispute: "no",
};

afterEach(() => {
  vi.restoreAllMocks();
  pushMock.mockReset();
  // localStorage is reset centrally in vitest.setup.ts (beforeEach).
});

describe("MatterCreateForm", () => {
  it("posts the payload with intake flags, shows the deadlines, then routes on demand", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(201, CREATED_MATTER));

    renderWithQuery(<MatterCreateForm />);

    await user.type(screen.getByLabelText("Client display name"), "Doe, Jane");
    await user.type(screen.getByLabelText("Incident date"), "2025-01-15");
    await user.type(screen.getByLabelText("Venue county (optional)"), "Maricopa");
    await answerIntake(user);
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
      public_entity_involved: "no",
      plaintiff_is_minor: "no",
      wrongful_death: "no",
      coverage_dispute: "no",
    });

    // WI-2 SOL visibility: the computed deadlines render BEFORE navigation.
    const createdCard = await screen.findByTestId("matter-created");
    expect(within(createdCard).getByTestId("deadline-banner")).toBeInTheDocument();
    expect(within(createdCard).getByTestId("deadline-item")).toHaveTextContent("2027-01-15");
    expect(pushMock).not.toHaveBeenCalled();

    // Navigation is the explicit second step.
    await user.click(screen.getByTestId("open-workspace"));
    expect(pushMock).toHaveBeenCalledWith("/matters/matter-123");
  });

  it("refuses submission inline while any intake question is unanswered", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch");

    renderWithQuery(<MatterCreateForm />);

    await user.type(screen.getByLabelText("Client display name"), "Doe, Jane");
    await user.type(screen.getByLabelText("Incident date"), "2025-01-15");
    // Answer only three of the four questions.
    for (const question of INTAKE_QUESTIONS.slice(0, 3)) {
      const group = screen.getByRole("group", { name: question });
      await user.click(within(group).getByLabelText("No"));
    }
    await user.click(screen.getByRole("button", { name: /create matter/i }));

    expect(await screen.findByTestId("intake-error")).toHaveTextContent(
      /answer all four intake questions/i,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders the matter_out_of_scope refusal per flag and does not route", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, {
        error: "matter_out_of_scope",
        detail: "matter is outside v1 supported scope (wrongful_death, coverage_dispute)",
        reasons: [
          {
            flag: "wrongful_death",
            answer: "yes",
            reason:
              "A wrongful-death claim is outside v1 supported scope — handle this matter in your existing workflow.",
          },
          {
            flag: "coverage_dispute",
            answer: "unknown",
            reason:
              "Confirm whether there is a coverage dispute, then create the matter — v1 accepts a matter only once this is answered 'no'.",
          },
        ],
      }),
    );

    renderWithQuery(<MatterCreateForm />);

    await user.type(screen.getByLabelText("Client display name"), "Roe, Rick");
    await user.type(screen.getByLabelText("Incident date"), "2025-02-01");
    await answerIntake(user, {
      "Is this a wrongful-death claim?": "Yes",
      "Is there a coverage dispute?": "Unknown",
    });
    await user.click(screen.getByRole("button", { name: /create matter/i }));

    const refusal = await screen.findByTestId("scope-refusal");
    const items = within(refusal).getAllByTestId("scope-reason");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveAttribute("data-flag", "wrongful_death");
    expect(items[0]).toHaveTextContent(/outside v1 supported scope/);
    expect(items[1]).toHaveAttribute("data-flag", "coverage_dispute");
    expect(items[1]).toHaveTextContent(/then create the matter/);
    expect(screen.queryByTestId("create-error")).not.toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
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
    await answerIntake(user);
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
      .mockResolvedValue(jsonResponse(201, { ...CREATED_MATTER, id: "m9" }));

    renderWithQuery(<MatterCreateForm />);
    await user.type(screen.getByLabelText("Client display name"), "No, Venue");
    await user.type(screen.getByLabelText("Incident date"), "2025-03-03");
    await answerIntake(user);
    await user.click(screen.getByRole("button", { name: /create matter/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).not.toHaveProperty("venue_county");
  });
});
