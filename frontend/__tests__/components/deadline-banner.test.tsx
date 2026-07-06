import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { DeadlineBanner } from "@/components/deadline-banner";
import type { DeadlineCandidate } from "@/lib/types";

const CANDIDATES: DeadlineCandidate[] = [
  {
    kind: "sol",
    date: "2027-05-01",
    statute_cite: "A.R.S. § 12-542",
    assumptions: ["discovery rule not applied"],
    verify_status: "unverified",
    confirmed: false,
  },
  {
    kind: "notice_of_claim",
    date: "2025-11-01",
    statute_cite: "A.R.S. § 12-821.01",
    assumptions: [],
    verify_status: "verified",
    confirmed: true,
  },
];

describe("DeadlineBanner", () => {
  it("renders each candidate with its date, cite, and verify badge", () => {
    render(<DeadlineBanner candidates={CANDIDATES} />);

    const items = screen.getAllByTestId("deadline-item");
    expect(items).toHaveLength(2);

    const sol = items[0];
    expect(within(sol).getByText("Statute of limitations")).toBeInTheDocument();
    expect(within(sol).getByText("2027-05-01")).toBeInTheDocument();
    expect(within(sol).getByText("A.R.S. § 12-542")).toBeInTheDocument();
    // unverified -> amber "pending counsel audit"
    expect(within(sol).getByText("Pending counsel audit")).toBeInTheDocument();
    expect(within(sol).getByText("Unconfirmed")).toBeInTheDocument();

    const noc = items[1];
    expect(within(noc).getByText("Counsel-verified")).toBeInTheDocument();
    expect(within(noc).getByText("Confirmed")).toBeInTheDocument();
  });

  it("renders nothing when there are no candidates", () => {
    const { container } = render(<DeadlineBanner candidates={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("has NO dismiss / close affordance (the non-dismissible rule)", () => {
    render(<DeadlineBanner candidates={CANDIDATES} />);
    const banner = screen.getByTestId("deadline-banner");

    // No button at all inside the banner.
    expect(within(banner).queryAllByRole("button")).toHaveLength(0);
    // And nothing labelled/aria-labelled as a close/dismiss control anywhere.
    expect(screen.queryByRole("button", { name: /close|dismiss/i })).toBeNull();
    expect(screen.queryByLabelText(/close|dismiss/i)).toBeNull();
  });
});
