import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { IntakeFlags } from "@/components/intake-flags";
import type { IntakeFlagAnswer } from "@/lib/types";

function matterWith(overrides: Partial<Record<string, IntakeFlagAnswer>> = {}) {
  return {
    public_entity_involved: "no" as IntakeFlagAnswer,
    plaintiff_is_minor: "no" as IntakeFlagAnswer,
    wrongful_death: "no" as IntakeFlagAnswer,
    coverage_dispute: "no" as IntakeFlagAnswer,
    ...overrides,
  };
}

describe("IntakeFlags", () => {
  it("renders all four stored answers in canonical order", () => {
    render(<IntakeFlags matter={matterWith()} />);

    const row = screen.getByTestId("intake-flags");
    const chips = within(row).getAllByTestId("intake-flag");
    expect(chips.map((c) => c.getAttribute("data-flag"))).toEqual([
      "public_entity_involved",
      "plaintiff_is_minor",
      "wrongful_death",
      "coverage_dispute",
    ]);
    expect(chips[0]).toHaveTextContent("Public entity: no");
    expect(chips[1]).toHaveTextContent("Minor plaintiff: no");
    expect(chips[2]).toHaveTextContent("Wrongful death: no");
    expect(chips[3]).toHaveTextContent("Coverage dispute: no");
  });

  it("marks a pre-preflight 'unknown' answer distinctly from 'no'", () => {
    render(<IntakeFlags matter={matterWith({ coverage_dispute: "unknown" })} />);

    const chips = screen.getAllByTestId("intake-flag");
    expect(chips[3]).toHaveAttribute("data-answer", "unknown");
    expect(chips[3]).toHaveTextContent("Coverage dispute: unknown");
    // 'no' chips and non-'no' chips must not share styling (outline vs warning variant).
    expect(chips[3].className).not.toBe(chips[0].className);
  });
});
