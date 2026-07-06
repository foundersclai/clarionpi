"use client";

/**
 * DemandGenerationCard — the `drafting` gate screen.
 *
 * "Generate demand letter" POSTs the Brain-2 demand-generation SSE run ({@link runDemandGeneration}).
 * Frames accumulate live, in arrival order:
 *   - `status {state:"started"}` / `status {state:"step", step:"memo"}` → a step trail;
 *   - per-section `section {section_id, rendered_preview}` → a growing list of section previews
 *     (heading + the RENDERED text — nothing token-shaped, the backend already resolved tokens);
 *   - `error {error:"section_validation_failed", section_id, violations}` → an inline per-section
 *     violation list, and the run CONTINUES (the remaining sections still draft);
 *   - a terminal `status {state:"draft_incomplete", failed_sections}` → the failed sections + a
 *     "Regenerate" that re-POSTs the run;
 *   - a real `gate_ready {gate:"compliance_review"}` → surfaced to the parent, which refetches to
 *     advance the view. The card NEVER advances the gate itself.
 *
 * Early typed errors (`wrong_gate_state` / `no_approved_plan` / `registry_drift` /
 * `budget_exceeded`) and trailing structural drifts (`compliance_snapshot_drift` /
 * `draft_registry_drift`) render inline as a run error — the run's own error path, never a torn
 * connection.
 */

import { useEffect, useRef, useState } from "react";
import { runDemandGeneration } from "@/lib/drafting";
import type { SseFrame } from "@/lib/sse";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export interface DemandGenerationCardProps {
  matterId: string;
  /** Called on a real `gate_ready` frame — the parent refetches to advance the view. */
  onGateReady?: (gate: string) => void;
}

/** One accumulated section preview (rendered text only). */
interface SectionPreview {
  section_id: string;
  rendered_preview: string;
}

/** One accumulated per-section validation failure (the run continued past it). */
interface SectionFailure {
  section_id: string;
  violations: string[];
}

const STATUS_LABELS: Record<string, string> = {
  started: "Started",
  step: "Working",
  draft_incomplete: "Draft incomplete",
  completed: "Completed",
};

export function DemandGenerationCard({ matterId, onGateReady }: DemandGenerationCardProps) {
  const [isRunning, setIsRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [memoStarted, setMemoStarted] = useState(false);
  const [previews, setPreviews] = useState<SectionPreview[]>([]);
  const [failures, setFailures] = useState<SectionFailure[]>([]);
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [failedSections, setFailedSections] = useState<string[] | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Abort an in-flight stream if the component unmounts mid-run.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  async function handleRun() {
    setIsRunning(true);
    setStarted(true);
    setMemoStarted(false);
    setPreviews([]);
    setFailures([]);
    setStatusLine("started");
    setFailedSections(null);
    setRunError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await runDemandGeneration(matterId, {
        signal: controller.signal,
        onEvent: (frame: SseFrame) => {
          const data = (frame.data ?? {}) as Record<string, unknown>;
          if (frame.event === "status") {
            const state = String(data.state ?? "");
            if (state === "step" && data.step === "memo") {
              setMemoStarted(true);
              setStatusLine("step");
            } else if (state === "draft_incomplete") {
              const failed = Array.isArray(data.failed_sections)
                ? (data.failed_sections as string[])
                : [];
              setFailedSections(failed);
              setStatusLine("draft_incomplete");
            } else if (state) {
              setStatusLine(state);
            }
          } else if (frame.event === "section") {
            // Accumulate the section preview in arrival order (rendered text only).
            const sectionId = String(data.section_id ?? "");
            const preview = String(data.rendered_preview ?? "");
            setPreviews((prev) => [...prev, { section_id: sectionId, rendered_preview: preview }]);
          } else if (frame.event === "error") {
            const code = String(data.error ?? "");
            if (code === "section_validation_failed") {
              // A per-section failure — surfaced inline; the run continues.
              const sectionId = String(data.section_id ?? "");
              const violations = Array.isArray(data.violations)
                ? (data.violations as string[])
                : [];
              setFailures((prev) => [...prev, { section_id: sectionId, violations }]);
            } else {
              // An early / structural run error — surfaced as the run error.
              setRunError(runErrorText(code, data));
            }
          } else if (frame.event === "gate_ready") {
            // ONLY a real gate_ready advances — surfaced to the parent, never advanced locally.
            onGateReady?.(String(data.gate ?? ""));
          }
        },
      });
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Generation connection failed.");
    } finally {
      setIsRunning(false);
      abortRef.current = null;
    }
  }

  const showTrail = started || previews.length > 0 || runError !== null;

  return (
    <Card data-testid="demand-generation-card">
      <CardHeader className="flex-row items-center justify-between">
        <div className="flex flex-col gap-1">
          <CardTitle>Draft the demand letter</CardTitle>
          <CardDescription>
            Generate the demand letter from the approved plan. Each section is drafted, validated,
            and rendered; a failed section is surfaced without stopping the run.
          </CardDescription>
        </div>
        <Button onClick={handleRun} disabled={isRunning} data-testid="generate-demand">
          {isRunning ? "Generating…" : "Generate demand letter"}
        </Button>
      </CardHeader>

      {showTrail && (
        <CardContent className="flex flex-col gap-4">
          {/* Step trail */}
          <div className="flex flex-wrap items-center gap-2" data-testid="generation-steps">
            <Badge variant={started ? "success" : "secondary"} data-step="started" data-done={started}>
              started{started ? " ✓" : ""}
            </Badge>
            <Badge
              variant={memoStarted ? "success" : "secondary"}
              data-step="memo"
              data-done={memoStarted}
            >
              memo{memoStarted ? " ✓" : ""}
            </Badge>
            {isRunning && (
              <span
                aria-hidden
                className="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent"
              />
            )}
            {statusLine && (
              <span className="text-xs text-ink-muted" data-testid="generation-status">
                {STATUS_LABELS[statusLine] ?? statusLine}
              </span>
            )}
          </div>

          {runError && (
            <p role="alert" data-testid="generation-error" className="text-sm text-danger">
              {runError}
            </p>
          )}

          {/* Live section previews (rendered text; nothing token-shaped). */}
          {previews.length > 0 && (
            <div className="flex flex-col gap-3" data-testid="section-previews">
              {previews.map((preview, index) => (
                <section
                  key={`${preview.section_id}-${index}`}
                  data-testid="section-preview"
                  data-section-id={preview.section_id}
                  className="flex flex-col gap-1 rounded-md border border-border p-3"
                >
                  <h4 className="text-sm font-semibold text-ink">{preview.section_id}</h4>
                  <p className="whitespace-pre-wrap text-sm text-ink">{preview.rendered_preview}</p>
                </section>
              ))}
            </div>
          )}

          {/* Per-section validation failures (the run continued past these). */}
          {failures.length > 0 && (
            <div
              data-testid="section-failures"
              className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger"
            >
              <p className="mb-1 font-medium">These sections failed validation and were surfaced:</p>
              <ul className="flex flex-col gap-2">
                {failures.map((failure, index) => (
                  <li
                    key={`${failure.section_id}-${index}`}
                    data-testid="section-failure"
                    data-section-id={failure.section_id}
                  >
                    <span className="font-medium">{failure.section_id}</span>
                    <ul className="ml-4 list-inside list-disc">
                      {failure.violations.map((violation, vIndex) => (
                        <li key={vIndex}>{violation}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Terminal draft_incomplete — the failed sections + a regenerate. */}
          {failedSections !== null && (
            <div
              data-testid="draft-incomplete"
              className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-foreground"
            >
              <p className="font-medium">
                The draft is incomplete — {failedSections.length} section(s) did not pass and the
                gate did not advance.
              </p>
              {failedSections.length > 0 && (
                <ul className="ml-1 list-inside list-disc" data-testid="failed-sections">
                  {failedSections.map((sectionId) => (
                    <li key={sectionId} data-section-id={sectionId}>
                      {sectionId}
                    </li>
                  ))}
                </ul>
              )}
              <div>
                <Button
                  size="sm"
                  onClick={handleRun}
                  disabled={isRunning}
                  data-testid="regenerate-demand"
                >
                  {isRunning ? "Regenerating…" : "Regenerate"}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}

/** Copy for an early / structural run error frame (verbatim detail preferred). */
function runErrorText(code: string, data: Record<string, unknown>): string {
  const detail = typeof data.detail === "string" ? data.detail : undefined;
  switch (code) {
    case "wrong_gate_state":
      return "This matter is not at the drafting step — refresh to see its current state.";
    case "no_approved_plan":
      return "No approved plan to draft from. Approve the plan at G2.5 first.";
    case "registry_drift":
    case "draft_registry_drift":
      return "The records changed since the plan was approved — re-confirm evidence and re-build the plan.";
    case "budget_exceeded":
      return detail ?? "The per-matter AI budget is exhausted; drafting stopped.";
    case "compliance_snapshot_drift":
      return "The compliance snapshot drifted during generation — regenerate to retry.";
    default:
      return detail ?? (code || "Generation error.");
  }
}
