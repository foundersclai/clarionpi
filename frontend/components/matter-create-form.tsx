"use client";

/**
 * MatterCreateForm — the workbench entry. Creates a matter via POST /api/matters and, on
 * 201, shows the created matter's rules-computed deadline candidates BEFORE navigation
 * (WI-2: an urgent SOL is visible before any document work) with an explicit "Open matter
 * workspace" action. On success it also invalidates the ["matters"] query so the home-page
 * matter list shows the new matter.
 *
 * Pilot-intake preflight (WI-2): four REQUIRED tri-state eligibility questions — no radio
 * is preselected (no silent defaults; the attorney answers explicitly), and submitting with
 * an unanswered question is refused inline without calling the API. The backend is the
 * authority on eligibility: any answer other than "no" returns a typed 422
 * (`matter_out_of_scope`) whose per-flag reasons render verbatim — the frontend surfaces
 * the backend's scope boundary, it does not invent a client-side rule.
 *
 * Typed-refusal demo: a non-AZ jurisdiction returns a 422 with `error:
 * "jurisdiction_unsupported"`; that code's message renders inline on the form.
 *
 * `jurisdiction` is a select (only AZ today, `supported` echoed from the refusal body when
 * present) and `claim_type` is fixed to "mva" (the MVP claim type).
 */

import { type FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiPost } from "@/lib/api";
import {
  INTAKE_FLAG_KEYS,
  type IntakeFlagAnswer,
  type IntakeFlagKey,
  type IntakeScopeReason,
  type MatterView,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DeadlineBanner } from "@/components/deadline-banner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface MatterCreatePayload {
  client_display_name: string;
  claim_type: "mva";
  incident_date: string;
  jurisdiction: string;
  venue_county?: string;
  public_entity_involved: IntakeFlagAnswer;
  plaintiff_is_minor: IntakeFlagAnswer;
  wrongful_death: IntakeFlagAnswer;
  coverage_dispute: IntakeFlagAnswer;
}

/** The intake questions, in the canonical flag order (labels are questions, not verdicts). */
const INTAKE_QUESTIONS: Record<IntakeFlagKey, string> = {
  public_entity_involved: "Is a public entity involved?",
  plaintiff_is_minor: "Is the plaintiff a minor?",
  wrongful_death: "Is this a wrongful-death claim?",
  coverage_dispute: "Is there a coverage dispute?",
};

const ANSWER_LABELS: Record<IntakeFlagAnswer, string> = {
  yes: "Yes",
  no: "No",
  unknown: "Unknown",
};

/** Map a typed refusal code to attorney-facing copy. Unknown codes fall back to detail. */
function refusalMessage(error: ApiError): string {
  const code = error.body.error;
  const supported = Array.isArray(error.body.supported)
    ? (error.body.supported as string[]).join(", ")
    : null;
  switch (code) {
    case "jurisdiction_unsupported":
      return supported
        ? `That jurisdiction isn't supported yet. Supported: ${supported}.`
        : "That jurisdiction isn't supported yet.";
    case "unauthenticated":
      return "You need to sign in before creating a matter.";
    default:
      return error.body.detail ?? "Could not create the matter. Please try again.";
  }
}

/** The per-flag reasons of a `matter_out_of_scope` refusal, or null for any other error. */
function scopeReasons(error: ApiError): IntakeScopeReason[] | null {
  if (error.body.error !== "matter_out_of_scope" || !Array.isArray(error.body.reasons)) {
    return null;
  }
  return error.body.reasons as IntakeScopeReason[];
}

export interface MatterCreateFormProps {
  /**
   * Called with the created matter when "Open matter workspace" is clicked. Defaults to
   * routing to the dashboard; a test can pass a spy to assert the payload/routing without
   * a full router mock.
   */
  onCreated?: (matter: MatterView) => void;
}

export function MatterCreateForm({ onCreated }: MatterCreateFormProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [clientName, setClientName] = useState("");
  const [incidentDate, setIncidentDate] = useState("");
  const [jurisdiction, setJurisdiction] = useState("AZ");
  const [venueCounty, setVenueCounty] = useState("");
  const [flags, setFlags] = useState<Record<IntakeFlagKey, IntakeFlagAnswer | null>>({
    public_entity_involved: null,
    plaintiff_is_minor: null,
    wrongful_death: null,
    coverage_dispute: null,
  });
  const [intakeError, setIntakeError] = useState<string | null>(null);

  const mutation = useMutation<MatterView, ApiError, MatterCreatePayload>({
    mutationFn: (payload) => apiPost<MatterView>("/api/matters", payload),
    onSuccess: () => {
      // The home-page list reads the real GET /api/matters — refetch it so the new matter shows.
      void queryClient.invalidateQueries({ queryKey: ["matters"] });
    },
  });

  function setFlag(key: IntakeFlagKey, answer: IntakeFlagAnswer) {
    setFlags((prev) => ({ ...prev, [key]: answer }));
    setIntakeError(null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Every intake question must be answered explicitly — nothing is defaulted silently.
    const unanswered = INTAKE_FLAG_KEYS.filter((key) => flags[key] === null);
    if (unanswered.length > 0) {
      setIntakeError("Answer all four intake questions before creating the matter.");
      return;
    }
    const payload: MatterCreatePayload = {
      client_display_name: clientName.trim(),
      claim_type: "mva",
      incident_date: incidentDate,
      jurisdiction,
      ...(venueCounty.trim() ? { venue_county: venueCounty.trim() } : {}),
      public_entity_involved: flags.public_entity_involved as IntakeFlagAnswer,
      plaintiff_is_minor: flags.plaintiff_is_minor as IntakeFlagAnswer,
      wrongful_death: flags.wrongful_death as IntakeFlagAnswer,
      coverage_dispute: flags.coverage_dispute as IntakeFlagAnswer,
    };
    mutation.mutate(payload);
  }

  const created = mutation.data;
  if (created) {
    // WI-2 SOL visibility: the computed deadlines render HERE, before any document work —
    // navigation to the workspace is an explicit second step.
    const openWorkspace = () => {
      if (onCreated) {
        onCreated(created);
      } else {
        router.push(`/matters/${created.id}`);
      }
    };
    return (
      <Card data-testid="matter-created">
        <CardHeader>
          <CardTitle>Matter created</CardTitle>
          <CardDescription>
            {created.client_display_name} · {created.jurisdiction} · incident{" "}
            {created.incident_date}. Review the computed deadlines before starting document
            work.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {created.deadline_candidates.length > 0 ? (
            <DeadlineBanner candidates={created.deadline_candidates} />
          ) : (
            <p className="text-sm text-ink-muted">
              No deadline candidates were computed for this matter.
            </p>
          )}
          <div>
            <Button onClick={openWorkspace} data-testid="open-workspace">
              Open matter workspace
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const error = mutation.error;
  const reasons = error ? scopeReasons(error) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>New matter</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4" aria-label="Create matter">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="client_display_name">Client display name</Label>
            <Input
              id="client_display_name"
              name="client_display_name"
              required
              value={clientName}
              onChange={(e) => setClientName(e.target.value)}
              placeholder="e.g. Doe, Jane"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="claim_type">Claim type</Label>
            {/* Fixed to MVA (the only supported claim type). Rendered read-only, not
                gray-disabled, so it reads as a stated fact rather than a blocked control. */}
            <Input id="claim_type" name="claim_type" value="Motor vehicle accident" readOnly />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="incident_date">Incident date</Label>
            <Input
              id="incident_date"
              name="incident_date"
              type="date"
              required
              value={incidentDate}
              onChange={(e) => setIncidentDate(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="jurisdiction">Jurisdiction</Label>
            <select
              id="jurisdiction"
              name="jurisdiction"
              value={jurisdiction}
              onChange={(e) => setJurisdiction(e.target.value)}
              className="flex h-9 w-full rounded-md border border-border bg-surface px-3 py-1 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <option value="AZ">Arizona (AZ)</option>
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="venue_county">Venue county (optional)</Label>
            <Input
              id="venue_county"
              name="venue_county"
              value={venueCounty}
              onChange={(e) => setVenueCounty(e.target.value)}
              placeholder="e.g. Maricopa"
            />
          </div>

          {/* WI-2 pilot-intake eligibility box — every question answered explicitly. */}
          <div
            className="flex flex-col gap-3 rounded-md border border-border p-3"
            data-testid="intake-section"
          >
            <div>
              <p className="text-sm font-medium text-ink">Pilot intake</p>
              <p className="text-xs text-ink-muted">
                v1 accepts a matter only when every answer is No. Answering Yes or Unknown
                shows the exact scope boundary — nothing is guessed on your behalf.
              </p>
            </div>
            {INTAKE_FLAG_KEYS.map((key) => (
              <fieldset key={key} className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <legend className="float-left mr-2 text-sm text-ink">
                  {INTAKE_QUESTIONS[key]}
                </legend>
                {(["yes", "no", "unknown"] as const).map((answer) => (
                  <label
                    key={answer}
                    className="flex items-center gap-1.5 text-sm text-ink"
                  >
                    <input
                      type="radio"
                      name={key}
                      value={answer}
                      checked={flags[key] === answer}
                      onChange={() => setFlag(key, answer)}
                      className="accent-accent"
                    />
                    {ANSWER_LABELS[answer]}
                  </label>
                ))}
              </fieldset>
            ))}
          </div>

          {intakeError && (
            <p role="alert" data-testid="intake-error" className="text-sm text-danger">
              {intakeError}
            </p>
          )}

          {error && reasons && (
            <div role="alert" data-testid="scope-refusal" className="flex flex-col gap-1">
              <p className="text-sm font-medium text-danger">
                This matter is outside v1 supported scope:
              </p>
              <ul className="flex list-disc flex-col gap-1 pl-5">
                {reasons.map((reason) => (
                  <li
                    key={reason.flag}
                    data-testid="scope-reason"
                    data-flag={reason.flag}
                    className="text-sm text-danger"
                  >
                    {reason.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && !reasons && (
            <p role="alert" data-testid="create-error" className="text-sm text-danger">
              {refusalMessage(error)}
            </p>
          )}

          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Creating…" : "Create matter"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
