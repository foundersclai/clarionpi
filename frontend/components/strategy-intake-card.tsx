"use client";

/**
 * StrategyIntakeCard — the G1.5 (strategy_intake) gate screen.
 *
 * A form over StrategyInputs: four free-text fields (liability_theory, injury_framing,
 * emphasis_notes, venue_posture) preserved EXACTLY (no trim — the strategy memo is the
 * attorney's voice), an `mmi_date` date input, and two MONEY inputs (anchor_amount,
 * property_damage_estimate) shown as dollars and converted to/from integer cents ONLY at the
 * wire boundary (lib/money). Unparseable / negative money is rejected inline — never sent.
 *
 * "Save" submits ONLY changed fields (`edit`); "Submit strategy & run analysis" approves with
 * the same inline-refusal pattern — the Approve button is ALWAYS clickable (no gray-out for a
 * legal block), the backend's 409/403 body renders verbatim, and `approve_blockers` renders as
 * an advisory list below.
 */

import { useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { GateStaleError, useSubmitGate } from "@/lib/gates";
import { MONEY_PARSE_ERROR, centsToDollars, dollarsToCents } from "@/lib/money";
import type {
  ApproveBlocker,
  RoleAffordances,
  StrategyIntakeEdits,
  StrategyIntakeVM,
} from "@/lib/types";
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

export interface StrategyIntakeCardProps {
  matterId: string;
  vm: StrategyIntakeVM;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
}

/** The editable form state — text as-is; money/date as raw input strings (parsed on submit). */
interface FormState {
  liability_theory: string;
  injury_framing: string;
  emphasis_notes: string;
  venue_posture: string;
  mmi_date: string;
  anchor_amount: string;
  property_damage_estimate: string;
}

function initialForm(vm: StrategyIntakeVM): FormState {
  const s = vm.strategy_inputs;
  return {
    liability_theory: s.liability_theory,
    injury_framing: s.injury_framing,
    emphasis_notes: s.emphasis_notes,
    venue_posture: s.venue_posture,
    mmi_date: s.mmi_date ?? "",
    anchor_amount: centsToDollars(s.anchor_amount_cents),
    property_damage_estimate: centsToDollars(s.property_damage_estimate_cents),
  };
}

function submitErrorText(error: ApiError | GateStaleError): string {
  if (error instanceof GateStaleError) {
    return error.message;
  }
  if (error.body.error === "role_forbidden") {
    const actor = actorRoleFrom(error.body.detail);
    return actor
      ? `Submitting strategy requires an attorney. (You are signed in as ${actor}.)`
      : "Submitting strategy requires an attorney.";
  }
  return error.body.detail ?? error.body.error ?? "Could not submit. Please try again.";
}

function actorRoleFrom(detail: string | undefined): string | null {
  if (!detail) return null;
  const match = /actor role is (\w+)/i.exec(detail);
  return match ? match[1] : null;
}

export function StrategyIntakeCard({
  matterId,
  vm,
  payloadVersion,
  roleAffordances,
}: StrategyIntakeCardProps) {
  const submit = useSubmitGate(matterId);
  const [form, setForm] = useState<FormState>(() => initialForm(vm));
  // Client-side money parse errors, keyed by field — block the submit, never send a guess.
  const [moneyErrors, setMoneyErrors] = useState<{
    anchor_amount?: string;
    property_damage_estimate?: string;
  }>({});

  function set<K extends keyof FormState>(key: K, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  const saved = vm.strategy_inputs;

  /**
   * Build the `edits` object with ONLY changed fields, converting money at the boundary.
   * Returns `{ edits }` on success, or `{ error }` naming the first money field that failed
   * to parse (so the caller can surface it inline and not submit).
   */
  function buildEdits():
    | { edits: StrategyIntakeEdits }
    | { error: { field: "anchor_amount" | "property_damage_estimate"; message: string } } {
    const edits: StrategyIntakeEdits = {};

    // Verbatim text — compare against the saved value with NO trimming.
    if (form.liability_theory !== saved.liability_theory)
      edits.liability_theory = form.liability_theory;
    if (form.injury_framing !== saved.injury_framing) edits.injury_framing = form.injury_framing;
    if (form.emphasis_notes !== saved.emphasis_notes) edits.emphasis_notes = form.emphasis_notes;
    if (form.venue_posture !== saved.venue_posture) edits.venue_posture = form.venue_posture;

    // mmi_date — empty string clears (null); a value sends the ISO date as-typed.
    const savedMmi = saved.mmi_date ?? "";
    if (form.mmi_date !== savedMmi) {
      edits.mmi_date = form.mmi_date === "" ? null : form.mmi_date;
    }

    // Money — strict parse; MONEY_PARSE_ERROR aborts (inline error, no request).
    const anchor = dollarsToCents(form.anchor_amount);
    if (anchor === MONEY_PARSE_ERROR) {
      return {
        error: { field: "anchor_amount", message: "Enter a valid dollar amount (e.g. 85,000.00)." },
      };
    }
    if (anchor !== saved.anchor_amount_cents) edits.anchor_amount_cents = anchor;

    const propDamage = dollarsToCents(form.property_damage_estimate);
    if (propDamage === MONEY_PARSE_ERROR) {
      return {
        error: {
          field: "property_damage_estimate",
          message: "Enter a valid dollar amount (e.g. 4,200.00).",
        },
      };
    }
    if (propDamage !== saved.property_damage_estimate_cents)
      edits.property_damage_estimate_cents = propDamage;

    return { edits };
  }

  function hasChanges(edits: StrategyIntakeEdits): boolean {
    return Object.keys(edits).length > 0;
  }

  function save() {
    const built = buildEdits();
    if ("error" in built) {
      setMoneyErrors({ [built.error.field]: built.error.message });
      return;
    }
    setMoneyErrors({});
    if (!hasChanges(built.edits)) return;
    submit.mutate({
      gate: "strategy_intake",
      body: { action: "edit", payload_version: payloadVersion, edits: built.edits },
    });
  }

  function approve() {
    // Validate money first — a bad amount blocks approval inline, before any request fires.
    const built = buildEdits();
    if ("error" in built) {
      setMoneyErrors({ [built.error.field]: built.error.message });
      return;
    }
    setMoneyErrors({});
    // ALWAYS fires the approve (server is authority; no client-side legal suppression). Unsaved
    // form changes ride along — the backend applies edits before the approve in one atomic call
    // (edit and approve both accept edits); dropping them here would silently discard what the
    // attorney typed.
    submit.mutate({
      gate: "strategy_intake",
      body: hasChanges(built.edits)
        ? { action: "approve", payload_version: payloadVersion, edits: built.edits }
        : { action: "approve", payload_version: payloadVersion },
    });
  }

  const submitError = submit.error ?? null;

  // Save is enabled when the form deviates from the saved values — OR when a money field is
  // unparseable, so clicking surfaces the inline error rather than a dead (gray-out) button.
  const canSave = useMemo(() => {
    const built = buildEdits();
    return "error" in built || hasChanges(built.edits);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form]);

  return (
    <Card data-testid="strategy-intake-card">
      <CardHeader>
        <CardTitle>Strategy intake (G1.5)</CardTitle>
        <CardDescription>
          Capture the liability theory, injury framing, and emphasis, then run the analysis.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {vm.deadlines_confirmed && (
          <p className="text-sm text-success" data-testid="deadlines-confirmed-context">
            Deadlines confirmed at facts review ✓
          </p>
        )}

        <Field
          id="liability_theory"
          label="Liability theory"
          value={form.liability_theory}
          onChange={(v) => set("liability_theory", v)}
        />
        <Field
          id="injury_framing"
          label="Injury framing"
          value={form.injury_framing}
          onChange={(v) => set("injury_framing", v)}
        />
        <Field
          id="emphasis_notes"
          label="Emphasis notes"
          value={form.emphasis_notes}
          onChange={(v) => set("emphasis_notes", v)}
        />
        <Field
          id="venue_posture"
          label="Venue posture"
          value={form.venue_posture}
          onChange={(v) => set("venue_posture", v)}
        />

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mmi_date">MMI date</Label>
          <Input
            id="mmi_date"
            name="mmi_date"
            type="date"
            value={form.mmi_date}
            onChange={(e) => set("mmi_date", e.target.value)}
          />
        </div>

        <MoneyField
          id="anchor_amount"
          label="Anchor amount"
          value={form.anchor_amount}
          error={moneyErrors.anchor_amount}
          onChange={(v) => set("anchor_amount", v)}
        />
        <MoneyField
          id="property_damage_estimate"
          label="Property damage estimate"
          value={form.property_damage_estimate}
          error={moneyErrors.property_damage_estimate}
          onChange={(v) => set("property_damage_estimate", v)}
        />

        {submitError && (
          <p role="alert" data-testid="strategy-submit-error" className="text-sm text-danger">
            {submitErrorText(submitError)}
          </p>
        )}

        <div className="flex flex-wrap gap-2 border-t border-border pt-4">
          <Button
            variant="outline"
            onClick={save}
            disabled={submit.isPending || !canSave}
          >
            {submit.isPending ? "Saving…" : "Save"}
          </Button>
          {/* ALWAYS clickable (no gray-out for a legal block). */}
          <Button onClick={approve} disabled={submit.isPending} data-testid="approve-strategy">
            Submit strategy &amp; run analysis
          </Button>
        </div>

        {roleAffordances.approve_blockers.length > 0 && (
          <BlockerAdvisory blockers={roleAffordances.approve_blockers} />
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------

/** A free-text field (textarea) — value flows through UNCHANGED (no trim on the boundary). */
function Field({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <textarea
        id={id}
        name={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
        className="flex w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
    </div>
  );
}

/** A money field — dollars in the UI; the owning form converts to cents at submit. */
function MoneyField({
  id,
  label,
  value,
  error,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  error: string | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label} (USD)</Label>
      <Input
        id={id}
        name={id}
        inputMode="decimal"
        placeholder="e.g. 85,000.00"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
      />
      {error && (
        <p role="alert" data-testid={`${id}-error`} className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

/** The advisory "what still blocks approval" panel (shared shape with the facts card). */
function BlockerAdvisory({ blockers }: { blockers: ApproveBlocker[] }) {
  return (
    <div
      data-testid="approve-blockers"
      className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-foreground"
    >
      <p className="mb-1 font-medium">Before this can be submitted:</p>
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
