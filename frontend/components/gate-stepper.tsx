"use client";

/**
 * GateStepper — a pure display of the matter's `gate_state` across the ten gate states.
 *
 * Design invariant (binding): the frontend DISPLAYS backend state, it never invents it.
 * This component derives everything from the `current` prop; it holds no internal "advance"
 * state and exposes no way to move the marker. The marker moves only when `current` changes
 * — i.e. when a real `gate_ready` frame or a refetch supplies a new gate_state and the
 * parent re-renders with it. There is deliberately no optimistic advancement.
 */

import { GATE_STATES, GATE_STATE_LABELS, type GateState } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface GateStepperProps {
  current: GateState;
}

type StepStatus = "past" | "current" | "future";

function stepStatus(index: number, currentIndex: number): StepStatus {
  if (index < currentIndex) return "past";
  if (index === currentIndex) return "current";
  return "future";
}

const DOT_CLASSES: Record<StepStatus, string> = {
  // past: filled but dim; current: solid accent, ring; future: hollow outline.
  past: "bg-ink-muted/40 text-surface border-transparent",
  current: "bg-accent text-accent-foreground border-accent ring-2 ring-accent/30",
  future: "bg-surface text-ink-muted border-border",
};

const LABEL_CLASSES: Record<StepStatus, string> = {
  past: "text-ink-muted/70",
  current: "text-ink font-semibold",
  future: "text-ink-muted",
};

export function GateStepper({ current }: GateStepperProps) {
  const currentIndex = GATE_STATES.indexOf(current);

  return (
    <ol
      className="flex flex-wrap gap-x-2 gap-y-3"
      aria-label="Matter gate progress"
      data-testid="gate-stepper"
      data-current={current}
    >
      {GATE_STATES.map((state, index) => {
        const status = stepStatus(index, currentIndex);
        return (
          <li
            key={state}
            data-state={state}
            data-status={status}
            aria-current={status === "current" ? "step" : undefined}
            className="flex min-w-[8.5rem] flex-1 items-center gap-2"
          >
            <span
              className={cn(
                "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
                "border text-xs font-semibold",
                DOT_CLASSES[status],
              )}
            >
              {index + 1}
            </span>
            <span className={cn("text-xs leading-tight", LABEL_CLASSES[status])}>
              {GATE_STATE_LABELS[state]}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
