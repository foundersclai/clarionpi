import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { GateStepper } from "@/components/gate-stepper";
import { GATE_STATES } from "@/lib/types";

describe("GateStepper", () => {
  it("renders all ten gate states", () => {
    render(<GateStepper current="corpus_processing" />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(GATE_STATES.length);
    expect(GATE_STATES).toHaveLength(10);
  });

  it("highlights the current state and marks it aria-current", () => {
    render(<GateStepper current="facts_review" />);
    const stepper = screen.getByTestId("gate-stepper");
    expect(stepper).toHaveAttribute("data-current", "facts_review");

    const current = stepper.querySelector('[data-status="current"]');
    expect(current).not.toBeNull();
    expect(current).toHaveAttribute("data-state", "facts_review");
    expect(current).toHaveAttribute("aria-current", "step");
  });

  it("classifies earlier states past and later states future", () => {
    render(<GateStepper current="drafting" />);
    const stepper = screen.getByTestId("gate-stepper");
    // corpus_processing precedes drafting -> past
    expect(
      stepper.querySelector('[data-state="corpus_processing"]'),
    ).toHaveAttribute("data-status", "past");
    // package_ready follows drafting -> future
    expect(
      stepper.querySelector('[data-state="package_ready"]'),
    ).toHaveAttribute("data-status", "future");
  });

  it("moves the marker ONLY when the current prop changes (no client-side advancement)", () => {
    const { rerender } = render(<GateStepper current="corpus_processing" />);
    let stepper = screen.getByTestId("gate-stepper");
    expect(stepper).toHaveAttribute("data-current", "corpus_processing");
    expect(
      stepper.querySelector('[data-state="corpus_processing"]'),
    ).toHaveAttribute("data-status", "current");

    // Simulate a gate_ready-driven refetch handing down the new backend state.
    rerender(<GateStepper current="facts_review" />);
    stepper = screen.getByTestId("gate-stepper");
    expect(stepper).toHaveAttribute("data-current", "facts_review");
    expect(
      stepper.querySelector('[data-state="facts_review"]'),
    ).toHaveAttribute("data-status", "current");
    // The prior state is now past, not still current.
    expect(
      stepper.querySelector('[data-state="corpus_processing"]'),
    ).toHaveAttribute("data-status", "past");
  });
});
