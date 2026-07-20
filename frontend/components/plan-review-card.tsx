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
 *     emphasis-directives list, and a per-section fact list (purpose, editable max_words, one row
 *     per citable fact showing its attorney-readable gloss with a "must cite" checkbox — token
 *     ids never render as text, only as row tooltips/data attributes; the wire still speaks
 *     BARE ids). Every resolvable fact row carries a "source" affordance opening the M6
 *     token-mode {@link ProvenanceViewer} (fact → source document page).
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
  TokenGlossView,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ProvenanceViewer } from "@/components/provenance-viewer";
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
      tokenGlosses={props.vm.token_glosses}
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

function emitErrorText(error: unknown): string {
  // React Query types the mutation error as ApiError, but at runtime ANY throw lands here — a
  // fetch-layer reject (server down, network blip, aborted request) is a bare TypeError with no
  // `.body`. Guard so a transient failure renders an inline message instead of crashing the card.
  if (!(error instanceof ApiError)) {
    return "Could not reach the server to build the plan. Check your connection and try again.";
  }
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
  /** BARE required-token ids, kept in the section's fact-universe order (never click order). */
  required: string[];
}

function initialSectionForm(section: PlannedSectionView): SectionFormState {
  return {
    max_words: String(section.max_words),
    required: [...section.required_tokens],
  };
}

/** Array equality by value+order (bare id lists). */
function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** Set equality (order-insensitive) — a must-cite toggle must never emit an order-only edit. */
function sameSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const bSet = new Set(b);
  return a.every((v) => bSet.has(v));
}

/**
 * The fact rows a section renders: its allowed tokens (plan order) plus any required id that is
 * no longer in the allowed set (registry drift / legacy free-text) — kept visible so the attorney
 * can uncheck it; flagged, never hidden.
 */
function sectionFactUniverse(section: PlannedSectionView, required: string[]): string[] {
  const universe = [...section.allowed_tokens];
  for (const id of required) {
    if (!universe.includes(id)) universe.push(id);
  }
  return universe;
}

function PlanPresentCard({
  matterId,
  plan,
  tokenGlosses,
  registryVersionCurrent,
  payloadVersion,
  roleAffordances,
}: {
  matterId: string;
  plan: PlanView;
  tokenGlosses: Record<string, TokenGlossView>;
  registryVersionCurrent: number;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
}) {
  const submit = useSubmitGate(matterId);
  // Re-propose: re-runs the strategist and emits a fresh (unapproved) plan version — the recovery
  // affordance for registry drift and for restoring the proposal after manual edits.
  const reEmit = useEmitPlan(matterId);

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
  // Source provenance: the bare token id a fact row's "source" affordance opened, or null.
  const [sourceToken, setSourceToken] = useState<string | null>(null);

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

      if (!sameSet(form.required, s.required_tokens)) {
        edit.required_tokens = form.required;
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
    // ALWAYS fires (server is authority; no client-side legal suppression). Unsaved form changes
    // ride along — the backend applies edits before the approve in one atomic call (edit and
    // approve both accept edits); dropping them here would silently approve the STALE plan and
    // discard what the attorney typed (e.g. the demand amount).
    submit.mutate({
      gate: "plan_review",
      body: hasChanges(built.edits)
        ? { action: "approve", payload_version: payloadVersion, edits: built.edits }
        : { action: "approve", payload_version: payloadVersion },
    });
  }

  const canSave = useMemo(() => {
    const built = buildEdits();
    return "error" in built || hasChanges(built.edits);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demandAmount, emphasis, sections]);

  const submitError = submit.error ?? null;

  function setSectionMaxWords(sectionId: string, value: string) {
    setSections((prev) => ({
      ...prev,
      [sectionId]: { ...prev[sectionId], max_words: value },
    }));
  }

  function toggleSectionRequired(section: PlannedSectionView, tokenId: string) {
    setSections((prev) => {
      const form = prev[section.section_id] ?? initialSectionForm(section);
      const next = new Set(form.required);
      if (next.has(tokenId)) {
        next.delete(tokenId);
      } else {
        next.add(tokenId);
      }
      // Deterministic order: the section's fact-universe order, never click order.
      const ordered = sectionFactUniverse(section, [...next]).filter((id) => next.has(id));
      return { ...prev, [section.section_id]: { ...form, required: ordered } };
    });
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
                glosses={tokenGlosses}
                form={sections[section.section_id] ?? initialSectionForm(section)}
                error={sectionErrors[section.section_id]}
                onMaxWords={(v) => setSectionMaxWords(section.section_id, v)}
                onToggleRequired={(tokenId) => toggleSectionRequired(section, tokenId)}
                onViewSource={setSourceToken}
              />
            ))}
          </ul>
        </div>

        {submitError && (
          <p role="alert" data-testid="plan-submit-error" className="text-sm text-danger">
            {planSubmitErrorText(submitError)}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
          <Button variant="outline" onClick={save} disabled={submit.isPending || !canSave}>
            {submit.isPending ? "Saving…" : "Save changes"}
          </Button>
          {/* ALWAYS clickable (no gray-out for a legal block). */}
          <Button onClick={approve} disabled={submit.isPending} data-testid="approve-plan">
            Approve plan &amp; start drafting
          </Button>
          <Button
            variant="ghost"
            onClick={() => reEmit.mutate()}
            disabled={reEmit.isPending || submit.isPending}
            data-testid="re-propose-plan"
            title="Runs the strategist again and emits a fresh proposal as a new plan version (manual edits are superseded)."
          >
            {reEmit.isPending ? "Re-proposing…" : "Re-propose plan"}
          </Button>
        </div>

        {reEmit.error && (
          <p role="alert" data-testid="plan-reemit-error" className="text-sm text-danger">
            {emitErrorText(reEmit.error)}
          </p>
        )}

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

      {/* M6 provenance — a fact row's "source" affordance opens the token-mode viewer (lazy
          fetch on open; the blob load inside is the audited PHI event, not this mount). */}
      <ProvenanceViewer
        matterId={matterId}
        open={sourceToken !== null}
        onClose={() => setSourceToken(null)}
        source={{ kind: "token", tokenId: sourceToken ?? "" }}
      />
    </Card>
  );
}

/** Turn a submit refusal into the inline copy (verbatim backend body preferred). */
function planSubmitErrorText(error: unknown): string {
  if (error instanceof GateStaleError) {
    return error.message;
  }
  // A fetch-layer reject (server down / network blip) is neither GateStaleError nor an ApiError
  // with a `.body` — guard so it renders inline rather than crashing the card on `.body.error`.
  if (!(error instanceof ApiError)) {
    return "Could not reach the server to submit. Check your connection and try again.";
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

/** "intro_and_representation" → "Intro and representation" (display only; ids stay on the wire). */
function sectionTitle(sectionId: string): string {
  const words = sectionId.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * One section row — purpose, editable max_words, and one row per citable fact: the attorney-
 * readable gloss with a "must cite" checkbox. Token ids never render as text — they live in the
 * row tooltip + data attributes only (the wire still speaks bare ids); a token with no gloss
 * entry falls back to its bare id rather than vanishing. A required id that is unresolvable or
 * outside the section's allowed set is flagged, not hidden, so it can be unchecked.
 */
function SectionRow({
  section,
  glosses,
  form,
  error,
  onMaxWords,
  onToggleRequired,
  onViewSource,
}: {
  section: PlannedSectionView;
  glosses: Record<string, TokenGlossView>;
  form: SectionFormState;
  error: string | undefined;
  onMaxWords: (value: string) => void;
  onToggleRequired: (tokenId: string) => void;
  onViewSource: (tokenId: string) => void;
}) {
  const maxWordsId = `max-words-${section.section_id}`;
  const universe = sectionFactUniverse(section, form.required);
  return (
    <li
      className="flex flex-col gap-2 rounded-md border border-border p-3"
      data-testid="plan-section-row"
      data-section-id={section.section_id}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-medium text-ink">{sectionTitle(section.section_id)}</span>
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

      {universe.length === 0 ? (
        <p className="text-xs text-ink-muted" data-testid="section-no-facts">
          No case facts may be cited in this section (boilerplate only).
        </p>
      ) : (
        <div className="flex flex-col gap-1.5" data-testid="section-facts">
          <p className="text-xs text-ink-muted">
            Facts this section may cite — check the ones the letter{" "}
            <span className="font-medium text-ink">must</span> cite:
          </p>
          <ul className="flex flex-col gap-1">
            {universe.map((tokenId) => {
              const gloss = glosses[tokenId];
              const label = gloss?.display_form ?? tokenId;
              const required = form.required.includes(tokenId);
              const unresolved = gloss ? !gloss.resolved : false;
              const foreign = !section.allowed_tokens.includes(tokenId);
              return (
                <li
                  key={tokenId}
                  title={tokenId}
                  data-testid="fact-row"
                  data-token-id={tokenId}
                  data-required={String(required)}
                  data-token-resolved={gloss ? String(gloss.resolved) : undefined}
                  className="flex items-start gap-2"
                >
                  <label className="flex flex-1 cursor-pointer items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={required}
                      onChange={() => onToggleRequired(tokenId)}
                      aria-label={`Must cite: ${label}`}
                    />
                    <span className="text-sm text-ink">{label}</span>
                    {gloss?.hint ? (
                      <span className="text-xs text-ink-muted">— {gloss.hint}</span>
                    ) : null}
                    {unresolved && <Badge variant="warning">no longer available</Badge>}
                    {foreign && !unresolved && (
                      <Badge variant="warning">not in this section&apos;s fact set</Badge>
                    )}
                  </label>
                  {/* Provenance click-through — outside the label so it never toggles the
                      checkbox. Hidden for a known-unresolved id (its lookup would dead-end). */}
                  {!unresolved && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="shrink-0"
                      onClick={() => onViewSource(tokenId)}
                      data-testid="fact-source"
                      aria-label={`View source: ${label}`}
                    >
                      source
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {error && (
        <p role="alert" data-testid={`section-error-${section.section_id}`} className="text-xs text-danger">
          {error}
        </p>
      )}
    </li>
  );
}
