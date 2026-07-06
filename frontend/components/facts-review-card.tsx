"use client";

/**
 * FactsReviewCard — the G1 (facts_review) gate screen.
 *
 * Renders the facts VM: a documents-summary chip row, a read-only incident-facts key/value
 * display, and the deadline list — one row per candidate with kind label, computed date
 * (prominent), statute cite, assumptions, a verify-status badge, and a Confirm checkbox.
 *
 * Confirmations stage LOCALLY (UI state, not invented backend state): a toggled-but-unsaved
 * row shows a "staged" dot next to its checkbox; "Save confirmations" submits ONLY the
 * changed rows as an `edit` action, then the envelope refetch redraws from backend truth.
 *
 * Approve honesty (binding design): the Approve button is ALWAYS clickable — never
 * gray-disabled for a legal block. Clicking ALWAYS fires the submit; the backend is the
 * authority, so its 409/403 body renders inline verbatim. `role_affordances.approve_blockers`
 * renders as an ADVISORY list below the button (so the attorney sees what still blocks before
 * clicking), but the advisory never suppresses the request.
 */

import { useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { GateStaleError, useSubmitGate } from "@/lib/gates";
import type {
  ApproveBlocker,
  DeadlineCandidateVM,
  DeadlineKind,
  FactsVM,
  RoleAffordances,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export interface FactsReviewCardProps {
  matterId: string;
  vm: FactsVM;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
}

const KIND_LABELS: Record<DeadlineKind, string> = {
  sol: "Statute of limitations",
  notice_of_claim: "Notice of claim",
};

function kindLabel(kind: DeadlineKind): string {
  return KIND_LABELS[kind] ?? kind;
}

/** Verify-status badge — amber "pending counsel audit" until a counsel audit verifies the rule. */
function VerifyBadge({ status }: { status: DeadlineCandidateVM["verify_status"] }) {
  if (status === "verified") {
    return <Badge variant="success">Counsel-verified</Badge>;
  }
  return <Badge variant="warning">Pending counsel audit</Badge>;
}

/** Turn any submit error into the copy the card shows inline (verbatim backend body preferred). */
function submitErrorText(error: ApiError | GateStaleError): string {
  if (error instanceof GateStaleError) {
    return error.message;
  }
  if (error.body.error === "role_forbidden") {
    // Derive the actor role from the typed detail — no hardcoded role assumption.
    const actor = actorRoleFrom(error.body.detail);
    return actor
      ? `Approving facts & deadlines requires an attorney. (You are signed in as ${actor}.)`
      : "Approving facts & deadlines requires an attorney.";
  }
  return error.body.detail ?? error.body.error ?? "Could not submit. Please try again.";
}

/** Best-effort pull of the actor's role from a role-guard detail string (never guesses one). */
function actorRoleFrom(detail: string | undefined): string | null {
  if (!detail) return null;
  const match = /actor role is (\w+)/i.exec(detail);
  return match ? match[1] : null;
}

export function FactsReviewCard({
  matterId,
  vm,
  payloadVersion,
  roleAffordances,
}: FactsReviewCardProps) {
  const submit = useSubmitGate(matterId);

  // Staged confirmations: rule_id -> desired confirmed value, only while it differs from the
  // saved (VM) value. Cleared implicitly on the next render after the envelope refetch.
  const [staged, setStaged] = useState<Record<string, boolean>>({});

  const savedByRule = useMemo(() => {
    const map: Record<string, boolean> = {};
    for (const c of vm.deadline_candidates) map[c.rule_id] = c.confirmed;
    return map;
  }, [vm.deadline_candidates]);

  function isChecked(candidate: DeadlineCandidateVM): boolean {
    return candidate.rule_id in staged ? staged[candidate.rule_id] : candidate.confirmed;
  }

  function isStaged(candidate: DeadlineCandidateVM): boolean {
    return (
      candidate.rule_id in staged && staged[candidate.rule_id] !== savedByRule[candidate.rule_id]
    );
  }

  function toggle(candidate: DeadlineCandidateVM, next: boolean) {
    setStaged((prev) => {
      const copy = { ...prev };
      if (next === savedByRule[candidate.rule_id]) {
        // Back to the saved value — no longer a pending change.
        delete copy[candidate.rule_id];
      } else {
        copy[candidate.rule_id] = next;
      }
      return copy;
    });
  }

  const changedConfirmations = useMemo(
    () =>
      Object.entries(staged)
        .filter(([rule_id, confirmed]) => confirmed !== savedByRule[rule_id])
        .map(([rule_id, confirmed]) => ({ rule_id, confirmed })),
    [staged, savedByRule],
  );

  function saveConfirmations() {
    if (changedConfirmations.length === 0) return;
    submit.mutate(
      {
        gate: "facts_review",
        body: {
          action: "edit",
          payload_version: payloadVersion,
          edits: { deadline_confirmations: changedConfirmations },
        },
      },
      { onSuccess: () => setStaged({}) },
    );
  }

  function approve() {
    // ALWAYS fires — the server is the authority (no client-side gray-out / suppression).
    submit.mutate({
      gate: "facts_review",
      body: { action: "approve", payload_version: payloadVersion },
    });
  }

  const submitError = submit.error ?? null;

  return (
    <Card data-testid="facts-review-card">
      <CardHeader>
        <CardTitle>Facts &amp; deadlines (G1)</CardTitle>
        <CardDescription>
          Confirm the rules-computed deadlines, then approve to advance to strategy intake.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {/* Documents summary */}
        <div className="flex flex-wrap gap-2" data-testid="documents-summary">
          <Badge variant="secondary">{vm.documents_summary.total} document(s)</Badge>
          {vm.documents_summary.needs_review > 0 && (
            <Badge variant="warning">{vm.documents_summary.needs_review} need review</Badge>
          )}
          {vm.documents_summary.failed > 0 && (
            <Badge variant="danger">{vm.documents_summary.failed} failed</Badge>
          )}
        </div>

        {/* Incident facts — READ-ONLY at M3. The API supports an incident_facts edit, but the
            coverage-table editing UX is undefined; we render the stored payload entries and
            defer the editor to a later milestone rather than invent a half-form here. */}
        <IncidentFactsView facts={vm.incident_facts} />

        {/* Deadline list */}
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium text-ink">Deadlines</p>
          {vm.deadline_candidates.length === 0 ? (
            <p className="text-sm text-ink-muted">No deadline candidates computed.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-border" data-testid="deadline-list">
              {vm.deadline_candidates.map((candidate) => {
                const inputId = `confirm-${candidate.rule_id}`;
                return (
                  <li
                    key={candidate.rule_id}
                    data-testid="deadline-row"
                    data-kind={candidate.kind}
                    className="flex flex-col gap-1 py-3"
                  >
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium text-ink">{kindLabel(candidate.kind)}</span>
                      <span className="font-mono text-base font-semibold text-ink">
                        {candidate.date}
                      </span>
                      <span className="text-xs text-ink-muted">{candidate.statute_cite}</span>
                      <VerifyBadge status={candidate.verify_status} />
                    </div>
                    {candidate.assumptions.length > 0 && (
                      <ul className="ml-1 list-inside list-disc text-xs text-ink-muted">
                        {candidate.assumptions.map((assumption, i) => (
                          <li key={i}>{assumption}</li>
                        ))}
                      </ul>
                    )}
                    <label htmlFor={inputId} className="flex items-center gap-2 text-sm text-ink">
                      <input
                        id={inputId}
                        type="checkbox"
                        checked={isChecked(candidate)}
                        onChange={(e) => toggle(candidate, e.target.checked)}
                        className="h-4 w-4 rounded border-border text-accent focus-visible:ring-2 focus-visible:ring-accent"
                      />
                      Confirmed
                      {isStaged(candidate) && (
                        <span
                          data-testid="staged-indicator"
                          title="Staged — not yet saved"
                          className="inline-block h-2 w-2 rounded-full bg-warning"
                          aria-label="staged, not yet saved"
                        />
                      )}
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          <div>
            <Button
              variant="outline"
              size="sm"
              onClick={saveConfirmations}
              disabled={submit.isPending || changedConfirmations.length === 0}
            >
              {submit.isPending ? "Saving…" : "Save confirmations"}
            </Button>
          </div>
        </div>

        {/* Approve — ALWAYS clickable (no gray-out for a legal block). */}
        <div className="flex flex-col gap-2 border-t border-border pt-4">
          <Button onClick={approve} disabled={submit.isPending} data-testid="approve-facts">
            Approve facts &amp; deadlines
          </Button>

          {/* Inline refusal — the backend body, verbatim-derived. */}
          {submitError && (
            <p role="alert" data-testid="facts-submit-error" className="text-sm text-danger">
              {submitErrorText(submitError)}
            </p>
          )}

          {/* Advisory blocker list — what the server would refuse right now. Never suppresses
              the request; it only tells the attorney what to expect. */}
          {roleAffordances.approve_blockers.length > 0 && (
            <BlockerAdvisory blockers={roleAffordances.approve_blockers} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------

/** Read-only projection of the incident-facts payload (deferral: no editor at M3). */
function IncidentFactsView({ facts }: { facts: FactsVM["incident_facts"] }) {
  const entries = facts ? Object.entries(facts.payload) : [];
  return (
    <div className="flex flex-col gap-1" data-testid="incident-facts">
      <p className="text-sm font-medium text-ink">Incident facts</p>
      {entries.length === 0 ? (
        <p className="text-sm text-ink-muted">No intake facts recorded yet.</p>
      ) : (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-ink-muted">{key}</dt>
              <dd className="text-ink">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/** The advisory "what still blocks approval" panel — maps each blocker to attorney copy. */
function BlockerAdvisory({ blockers }: { blockers: ApproveBlocker[] }) {
  return (
    <div
      data-testid="approve-blockers"
      className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-foreground"
    >
      <p className="mb-1 font-medium">Before this can be approved:</p>
      <ul className="flex list-inside list-disc flex-col gap-0.5">
        {blockers.map((blocker) => (
          <li key={blocker.guard} data-guard={blocker.guard} data-code={blocker.code}>
            {blocker.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}
