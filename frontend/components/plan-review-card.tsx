"use client";

/**
 * PlanReviewCard — the G2.5 (plan_review) gate screen.
 *
 * Two shapes off the {@link PlanReviewVM}:
 *   - `plan_missing` → an explainer + "Build plan (runs the strategist)" — {@link useEmitPlan}
 *     POSTs the non-SSE emit, shows a pending state (seconds-long), and the envelope refetch
 *     redraws with the freshly emitted (unapproved) plan.
 *   - a plan is present → the drafting contract as an editable form: the demand amount (dollars ↔
 *     cents at the wire boundary — lib/money), a fixed `demand_type` "open" chip, an editable
 *     emphasis-directives list, and a per-section table (purpose, editable max_words, read-only
 *     allowed-token chips as BARE ids, and a small comma-separated required-token editor).
 *
 * Save submits ONLY changed fields as a gates `edit` action — which RE-EMITS a new UNAPPROVED
 * plan version (N+1); the card renders the current version + an "unapproved changes" badge when
 * `approved` is false. Approve is ALWAYS clickable (no gray-out for a legal block); the backend's
 * 409/403/422 body renders inline verbatim (a `strategy_plan` guard `plan_registry_drift` maps to
 * "records changed since this plan was drafted — re-emit").
 *
 * Nothing token-shaped renders: token chips show the BARE registry ids the wire already sends.
 */

import { useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { GateStaleError, useSubmitGate } from "@/lib/gates";
import { useEmitPlan } from "@/lib/drafting";
import { MONEY_PARSE_ERROR, centsToDollars, dollarsToCents } from "@/lib/money";
import type {
  PlanReviewEdits,
  PlanReviewVM,
  PlanView,
  PlannedSectionEdit,
  PlannedSectionView,
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export interface PlanReviewCardProps {
  matterId: string;
  vm: PlanReviewVM;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
}

export function PlanReviewCard(props: PlanReviewCardProps) {
  if (props.vm.plan_missing || props.vm.plan === null) {
    return <PlanMissingCard matterId={props.matterId} />;
  }
  return (
    <PlanPresentCard
      matterId={props.matterId}
      plan={props.vm.plan}
      registryVersionCurrent={props.vm.registry_version_current}
      payloadVersion={props.payloadVersion}
      roleAffordances={props.roleAffordances}
    />
  );
}

// ---------------------------------------------------------------------------------------
// plan_missing — build the plan (runs the strategist; non-SSE emit).
// ---------------------------------------------------------------------------------------

function PlanMissingCard({ matterId }: { matterId: string }) {
  const emit = useEmitPlan(matterId);
  const error = emit.error;
  const errorText = error ? emitErrorText(error) : null;

  return (
    <Card data-testid="plan-review-card" data-plan-missing="true">
      <CardHeader>
        <CardTitle>Plan review (G2.5)</CardTitle>
        <CardDescription>
          No drafting plan has been built yet. Build the plan to have the strategist propose the
          demand amount, the emphasis directives, and the per-section token budget — then review and
          approve it before drafting.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div>
          <Button onClick={() => emit.mutate()} disabled={emit.isPending} data-testid="build-plan">
            {emit.isPending ? "Building plan…" : "Build plan (runs the strategist)"}
          </Button>
        </div>
        {emit.isPending && (
          <p className="text-xs text-ink-muted" data-testid="build-plan-pending">
            The strategist is drafting the plan — this can take a few seconds.
          </p>
        )}
        {errorText && (
          <p role="alert" data-testid="plan-emit-error" className="text-sm text-danger">
            {errorText}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function emitErrorText(error: ApiError): string {
  if (error.body.error === "letter_structure_missing") {
    return "This jurisdiction has no demand-letter skeleton, so a plan cannot be built. Contact an administrator.";
  }
  if (error.body.error === "gate_state_mismatch") {
    return "This matter is no longer at plan review — refresh to see its current state.";
  }
  return error.body.detail ?? error.body.error ?? "Could not build the plan. Please try again.";
}

// ---------------------------------------------------------------------------------------
// plan present — the editable drafting contract.
// ---------------------------------------------------------------------------------------

interface SectionFormState {
  max_words: string;
  /** Comma-separated BARE required-token ids, as typed. */
  required_tokens: string;
}

function initialSectionForm(section: PlannedSectionView): SectionFormState {
  return {
    max_words: String(section.max_words),
    required_tokens: section.required_tokens.join(", "),
  };
}

/** Parse a comma-separated bare-id list into a trimmed, non-empty, de-duplicated array. */
function parseTokenList(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const id = part.trim();
    if (id.length > 0 && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

/** Array equality by value+order (bare id lists). */
function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function PlanPresentCard({
  matterId,
  plan,
  registryVersionCurrent,
  payloadVersion,
  roleAffordances,
}: {
  matterId: string;
  plan: PlanView;
  registryVersionCurrent: number;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
}) {
  const submit = useSubmitGate(matterId);

  const [demandAmount, setDemandAmount] = useState(() =>
    centsToDollars(plan.demand_amount_cents),
  );
  const [emphasis, setEmphasis] = useState<string[]>(() => [...plan.emphasis_directives]);
  const [sections, setSections] = useState<Record<string, SectionFormState>>(() => {
    const map: Record<string, SectionFormState> = {};
    for (const s of plan.sections) map[s.section_id] = initialSectionForm(s);
    return map;
  });
  const [moneyError, setMoneyError] = useState<string | null>(null);
  const [sectionErrors, setSectionErrors] = useState<Record<string, string>>({});

  const registryDrift = plan.registry_version !== registryVersionCurrent;

  /**
   * Build the `edits` with ONLY changed fields. Returns `{ edits }`, or `{ error }` with the first
   * validation failure (money / a non-integer max_words) so the caller surfaces it and sends nothing.
   */
  function buildEdits():
    | { edits: PlanReviewEdits }
    | { error: { kind: "money" | "section"; sectionId?: string; message: string } } {
    const edits: PlanReviewEdits = {};

    // Demand amount — strict parse; MONEY_PARSE_ERROR aborts (inline error, no request).
    const amount = dollarsToCents(demandAmount);
    if (amount === MONEY_PARSE_ERROR) {
      return {
        error: { kind: "money", message: "Enter a valid dollar amount (e.g. 250,000.00)." },
      };
    }
    if (amount !== plan.demand_amount_cents) edits.demand_amount_cents = amount;

    // Emphasis directives — a list-equality check against the saved value.
    if (!sameList(emphasis, plan.emphasis_directives)) {
      edits.emphasis_directives = emphasis;
    }

    // Per-section edits — only sections whose max_words or required-tokens changed.
    const sectionEdits: PlannedSectionEdit[] = [];
    for (const s of plan.sections) {
      const form = sections[s.section_id];
      if (!form) continue;
      const edit: PlannedSectionEdit = { section_id: s.section_id };
      let changed = false;

      const trimmedWords = form.max_words.trim();
      if (trimmedWords !== String(s.max_words)) {
        if (!/^\d+$/.test(trimmedWords) || Number(trimmedWords) <= 0) {
          return {
            error: {
              kind: "section",
              sectionId: s.section_id,
              message: "Max words must be a positive whole number.",
            },
          };
        }
        edit.max_words = Number(trimmedWords);
        changed = true;
      }

      const required = parseTokenList(form.required_tokens);
      if (!sameList(required, s.required_tokens)) {
        edit.required_tokens = required;
        changed = true;
      }

      if (changed) sectionEdits.push(edit);
    }
    if (sectionEdits.length > 0) edits.sections = sectionEdits;

    return { edits };
  }

  function hasChanges(edits: PlanReviewEdits): boolean {
    return Object.keys(edits).length > 0;
  }

  function applyBuildError(err: {
    kind: "money" | "section";
    sectionId?: string;
    message: string;
  }): void {
    if (err.kind === "money") {
      setMoneyError(err.message);
      setSectionErrors({});
    } else {
      setMoneyError(null);
      setSectionErrors(err.sectionId ? { [err.sectionId]: err.message } : {});
    }
  }

  function save() {
    const built = buildEdits();
    if ("error" in built) {
      applyBuildError(built.error);
      return;
    }
    setMoneyError(null);
    setSectionErrors({});
    if (!hasChanges(built.edits)) return;
    submit.mutate({
      gate: "plan_review",
      body: { action: "edit", payload_version: payloadVersion, edits: built.edits },
    });
  }

  function approve() {
    // Validate the form first — a bad value blocks approval inline, before any request fires.
    const built = buildEdits();
    if ("error" in built) {
      applyBuildError(built.error);
      return;
    }
    setMoneyError(null);
    setSectionErrors({});
    // ALWAYS fires (server is authority; no client-side legal suppression).
    submit.mutate({
      gate: "plan_review",
      body: { action: "approve", payload_version: payloadVersion },
    });
  }

  const canSave = useMemo(() => {
    const built = buildEdits();
    return "error" in built || hasChanges(built.edits);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demandAmount, emphasis, sections]);

  const submitError = submit.error ?? null;

  function setSectionField(sectionId: string, key: keyof SectionFormState, value: string) {
    setSections((prev) => ({
      ...prev,
      [sectionId]: { ...prev[sectionId], [key]: value },
    }));
  }

  return (
    <Card data-testid="plan-review-card" data-plan-version={plan.version}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Plan review (G2.5)
          <Badge variant="secondary" data-testid="plan-version">
            v{plan.version}
          </Badge>
          {!plan.approved && (
            <Badge variant="warning" data-testid="unapproved-badge">
              unapproved changes
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          Review the drafting contract — the demand amount, the emphasis, and the per-section token
          budget. Saving an edit re-emits a new unapproved plan version; approve to start drafting.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {registryDrift && (
          <p
            role="alert"
            data-testid="plan-registry-drift"
            className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs text-warning-foreground"
          >
            The records changed since this plan was drafted (plan v.registry {plan.registry_version}{" "}
            vs current {registryVersionCurrent}). Re-build the plan before approving.
          </p>
        )}

        {/* Demand amount + type */}
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="demand_amount">Demand amount (USD)</Label>
            <Input
              id="demand_amount"
              name="demand_amount"
              inputMode="decimal"
              placeholder="e.g. 250,000.00"
              value={demandAmount}
              onChange={(e) => setDemandAmount(e.target.value)}
              aria-invalid={moneyError ? true : undefined}
            />
            {moneyError && (
              <p role="alert" data-testid="demand_amount-error" className="text-xs text-danger">
                {moneyError}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Demand type</Label>
            <Badge variant="outline" data-testid="demand-type">
              {plan.demand_type}
            </Badge>
          </div>
        </div>

        {/* Emphasis directives */}
        <EmphasisEditor directives={emphasis} onChange={setEmphasis} />

        {/* Per-section token budget */}
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium text-ink">Sections</p>
          <ul className="flex flex-col gap-3" data-testid="plan-sections">
            {plan.sections.map((section) => (
              <SectionRow
                key={section.section_id}
                section={section}
                form={sections[section.section_id] ?? initialSectionForm(section)}
                error={sectionErrors[section.section_id]}
                onMaxWords={(v) => setSectionField(section.section_id, "max_words", v)}
                onRequired={(v) => setSectionField(section.section_id, "required_tokens", v)}
              />
            ))}
          </ul>
        </div>

        {submitError && (
          <p role="alert" data-testid="plan-submit-error" className="text-sm text-danger">
            {planSubmitErrorText(submitError)}
          </p>
        )}

        <div className="flex flex-wrap gap-2 border-t border-border pt-4">
          <Button variant="outline" onClick={save} disabled={submit.isPending || !canSave}>
            {submit.isPending ? "Saving…" : "Save changes"}
          </Button>
          {/* ALWAYS clickable (no gray-out for a legal block). */}
          <Button onClick={approve} disabled={submit.isPending} data-testid="approve-plan">
            Approve plan &amp; start drafting
          </Button>
        </div>

        {roleAffordances.approve_blockers.length > 0 && (
          <div
            data-testid="approve-blockers"
            className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-foreground"
          >
            <p className="mb-1 font-medium">Before this can be approved:</p>
            <ul className="flex list-inside list-disc flex-col gap-0.5">
              {roleAffordances.approve_blockers.map((b) => (
                <li key={b.guard} data-guard={b.guard} data-code={b.code}>
                  {b.detail}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Turn a submit refusal into the inline copy (verbatim backend body preferred). */
function planSubmitErrorText(error: ApiError | GateStaleError): string {
  if (error instanceof GateStaleError) {
    return error.message;
  }
  if (error.body.error === "role_forbidden") {
    const actor = actorRoleFrom(error.body.detail);
    return actor
      ? `Approving the plan requires an attorney. (You are signed in as ${actor}.)`
      : "Approving the plan requires an attorney.";
  }
  if (error.body.error === "guard_failed" && error.body.guard === "strategy_plan") {
    if (error.body.code === "plan_missing") {
      return "No plan exists to approve — build the plan first.";
    }
    if (error.body.code === "plan_registry_drift") {
      return "The records changed since this plan was drafted — re-build the plan before approving.";
    }
  }
  if (error.body.error === "unknown_plan_section") {
    const id = typeof error.body.section_id === "string" ? error.body.section_id : "";
    return `A section edit named a section that is not in the plan${id ? ` (${id})` : ""}.`;
  }
  return error.body.detail ?? error.body.error ?? "Could not submit. Please try again.";
}

function actorRoleFrom(detail: string | undefined): string | null {
  if (!detail) return null;
  const match = /actor role is (\w+)/i.exec(detail);
  return match ? match[1] : null;
}

// ---------------------------------------------------------------------------------------

/** An editable list of emphasis directives — add / edit / remove free-text lines. */
function EmphasisEditor({
  directives,
  onChange,
}: {
  directives: string[];
  onChange: (next: string[]) => void;
}) {
  function update(index: number, value: string) {
    onChange(directives.map((d, i) => (i === index ? value : d)));
  }
  function remove(index: number) {
    onChange(directives.filter((_, i) => i !== index));
  }
  function add() {
    onChange([...directives, ""]);
  }

  return (
    <div className="flex flex-col gap-2" data-testid="emphasis-editor">
      <p className="text-sm font-medium text-ink">Emphasis directives</p>
      {directives.length === 0 ? (
        <p className="text-xs text-ink-muted">No emphasis directives.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {directives.map((directive, index) => (
            <li key={index} className="flex items-center gap-2" data-testid="emphasis-row">
              <Input
                aria-label={`Emphasis directive ${index + 1}`}
                value={directive}
                onChange={(e) => update(index, e.target.value)}
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => remove(index)}
                aria-label={`Remove emphasis directive ${index + 1}`}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div>
        <Button variant="outline" size="sm" onClick={add} data-testid="add-emphasis">
          Add directive
        </Button>
      </div>
    </div>
  );
}

/** One section row — purpose, editable max_words, read-only allowed chips, required-token editor. */
function SectionRow({
  section,
  form,
  error,
  onMaxWords,
  onRequired,
}: {
  section: PlannedSectionView;
  form: SectionFormState;
  error: string | undefined;
  onMaxWords: (value: string) => void;
  onRequired: (value: string) => void;
}) {
  const maxWordsId = `max-words-${section.section_id}`;
  const requiredId = `required-${section.section_id}`;
  return (
    <li
      className="flex flex-col gap-2 rounded-md border border-border p-3"
      data-testid="plan-section-row"
      data-section-id={section.section_id}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-medium text-ink">{section.section_id}</span>
        <span className="text-sm text-ink-muted">{section.purpose}</span>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div className="flex w-28 flex-col gap-1">
          <Label htmlFor={maxWordsId}>Max words</Label>
          <Input
            id={maxWordsId}
            inputMode="numeric"
            value={form.max_words}
            onChange={(e) => onMaxWords(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        </div>
      </div>

      <TokenChips label="Allowed tokens" tokens={section.allowed_tokens} testid="allowed-tokens" />

      <div className="flex flex-col gap-1">
        <Label htmlFor={requiredId}>Required tokens (comma-separated ids)</Label>
        <Input
          id={requiredId}
          value={form.required_tokens}
          onChange={(e) => onRequired(e.target.value)}
          placeholder="e.g. FACT_3, AMT_1"
        />
        <TokenChips
          label="Currently required"
          tokens={parseTokenList(form.required_tokens)}
          testid="required-tokens"
        />
      </div>

      {error && (
        <p role="alert" data-testid={`section-error-${section.section_id}`} className="text-xs text-danger">
          {error}
        </p>
      )}
    </li>
  );
}

/** A read-only row of BARE token-id chips (never token-shaped — the wire sends bare ids). */
function TokenChips({
  label,
  tokens,
  testid,
}: {
  label: string;
  tokens: string[];
  testid: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1" data-testid={testid}>
      <span className="text-xs text-ink-muted">{label}:</span>
      {tokens.length === 0 ? (
        <span className="text-xs text-ink-muted">none</span>
      ) : (
        tokens.map((token) => (
          <Badge key={token} variant="secondary" data-token-id={token}>
            {token}
          </Badge>
        ))
      )}
    </div>
  );
}
