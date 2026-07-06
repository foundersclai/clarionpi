"use client";

/**
 * MatterCreateForm — the workbench entry. Creates a matter via POST /api/matters and, on
 * 201, routes to its dashboard. On success it also invalidates the ["matters"] query so the
 * home-page matter list (now backed by the real GET /api/matters) shows the new matter.
 *
 * Typed-refusal demo: a non-AZ jurisdiction returns a 422 with `error:
 * "jurisdiction_unsupported"`. We render that code's message inline on the form — the
 * frontend surfaces the backend's typed refusal, it does not invent a client-side rule.
 *
 * `jurisdiction` is a select (only AZ today, `supported` echoed from the refusal body when
 * present) and `claim_type` is fixed to "mva" (the MVP claim type).
 */

import { type FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiPost } from "@/lib/api";
import type { MatterView } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface MatterCreatePayload {
  client_display_name: string;
  claim_type: "mva";
  incident_date: string;
  jurisdiction: string;
  venue_county?: string;
}

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

export interface MatterCreateFormProps {
  /**
   * Called with the created matter after a 201. Defaults to routing to the dashboard; a
   * test can pass a spy to assert the payload/routing without a full router mock.
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

  const mutation = useMutation<MatterView, ApiError, MatterCreatePayload>({
    mutationFn: (payload) => apiPost<MatterView>("/api/matters", payload),
    onSuccess: (matter) => {
      // The home-page list reads the real GET /api/matters — refetch it so the new matter shows.
      void queryClient.invalidateQueries({ queryKey: ["matters"] });
      if (onCreated) {
        onCreated(matter);
      } else {
        router.push(`/matters/${matter.id}`);
      }
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: MatterCreatePayload = {
      client_display_name: clientName.trim(),
      claim_type: "mva",
      incident_date: incidentDate,
      jurisdiction,
      ...(venueCounty.trim() ? { venue_county: venueCounty.trim() } : {}),
    };
    mutation.mutate(payload);
  }

  const error = mutation.error;

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

          {error && (
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
