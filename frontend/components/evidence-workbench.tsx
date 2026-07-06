"use client";

/**
 * EvidenceWorkbench — the G2a (evidence_review) surface.
 *
 * Six Cards over the evidence_review view-model (analysis banner, chronology, ledger, risk flags,
 * exhibits, confirm bar). Binding design rules, enforced here:
 *   - Display backend state, never invent it — every value comes from the VM / a mutation response.
 *   - Blocked actions stay CLICKABLE with inline backend reasons (no gray-outs for a legal block).
 *   - Submit bodies are CLOSED (no VM echo); nothing token-shaped renders (bare `exhibit_token_id`).
 *   - Money renders from integer cents via `centsToDollars`; the FE NEVER sums (the ledger grid
 *     refetches the server total, and a billing edit replaces the display from the response).
 *
 * `analysis_running` renders this same workbench with the run button front-and-center (the matter
 * parks there until someone runs it); only a real `gate_ready` frame advances the view (via the
 * parent's envelope refetch on `onGateReady`).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { GateStaleError, useSubmitGate } from "@/lib/gates";
import { centsToDollars, dollarsToCents, MONEY_PARSE_ERROR } from "@/lib/money";
import { formatPageRanges, parsePageRanges, PageRangeError } from "@/lib/pages";
import {
  getManifest,
  runAnalysis,
  useBillingEdits,
  useBillingLines,
  useChronologyOverlay,
  useExhibitPick,
  useFlagDisposition,
  usePhiDisposition,
} from "@/lib/evidence";
import type { SseFrame } from "@/lib/sse";
import type {
  BillingLine,
  BillingLineEdit,
  ChronologyOverlayBody,
  ChronologyRow,
  EvidenceReviewVM,
  ExhibitEntry,
  FlagDisposition,
  LedgerCategory,
  LedgerColumns,
  LedgerVM,
  ManifestResponse,
  PhiDisposition,
  RiskFlagVM,
  RoleAffordances,
} from "@/lib/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";
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

// ---------------------------------------------------------------------------------------
// Closed vocabularies (mirror the backend StrEnum values — labels are display-only).
// ---------------------------------------------------------------------------------------

const LEDGER_CATEGORIES: LedgerCategory[] = [
  "er",
  "ambulance",
  "imaging",
  "pt_chiro",
  "ortho",
  "surgery",
  "pharmacy",
  "other",
];

const CATEGORY_LABELS: Record<string, string> = {
  er: "ER",
  ambulance: "Ambulance",
  imaging: "Imaging",
  pt_chiro: "PT / Chiro",
  ortho: "Ortho",
  surgery: "Surgery",
  pharmacy: "Pharmacy",
  other: "Other",
};

const FLAG_KIND_LABELS: Record<string, string> = {
  treatment_gap: "Treatment gap",
  preexisting_condition: "Pre-existing condition",
  prior_claim: "Prior claim",
  degenerative_finding: "Degenerative finding",
  causation_ambiguity: "Causation ambiguity",
  liability_weakness: "Liability weakness",
  low_property_damage: "Low property damage",
  third_party_phi: "Third-party PHI",
};

const DETECTOR_LABELS: Record<string, string> = {
  date_math: "date math",
  label: "AI label",
  heuristic_llm: "AI heuristic",
};

const DISPOSITION_LABELS: Record<FlagDisposition, string> = {
  address_in_letter: "Address in letter",
  omit_with_rationale: "Omit (with rationale)",
  need_more_records: "Need more records",
};

const FLAG_DISPOSITIONS: FlagDisposition[] = [
  "address_in_letter",
  "omit_with_rationale",
  "need_more_records",
];

const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

function labelOf(map: Record<string, string>, key: string): string {
  return map[key] ?? key;
}

// ---------------------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------------------

export interface EvidenceWorkbenchProps {
  matterId: string;
  vm: EvidenceReviewVM;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
  /** True when the matter parks at `analysis_running` (analysis not yet run). */
  analysisRunning: boolean;
  /** Called on a real `gate_ready` frame — the parent refetches to advance the view. */
  onGateReady?: (gate: string) => void;
}

export function EvidenceWorkbench({
  matterId,
  vm,
  payloadVersion,
  roleAffordances,
  analysisRunning,
  onGateReady,
}: EvidenceWorkbenchProps) {
  return (
    <div className="flex flex-col gap-4" data-testid="evidence-workbench">
      <AnalysisBanner
        matterId={matterId}
        analysisRunning={analysisRunning}
        onGateReady={onGateReady}
      />

      {/* At analysis_running the derived surfaces below are not yet built — the run banner is the
          whole story until a gate_ready advances us. */}
      {!analysisRunning && (
        <>
          <ChronologyPanel matterId={matterId} chronology={vm.chronology} />
          <LedgerPanel matterId={matterId} ledger={vm.ledger} />
          <RiskFlagsPanel
            matterId={matterId}
            flags={vm.risk_flags}
            roleAffordances={roleAffordances}
          />
          <ExhibitsPanel matterId={matterId} exhibits={vm.exhibits} />
          <ConfirmBar
            matterId={matterId}
            payloadVersion={payloadVersion}
            dedupPending={vm.dedup_pending}
          />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// (1) Analysis banner
// ---------------------------------------------------------------------------------------

const ANALYSIS_STEPS: readonly string[] = ["registry_sync", "chronology", "ledger", "risk_flags"];

function AnalysisBanner({
  matterId,
  analysisRunning,
  onGateReady,
}: {
  matterId: string;
  analysisRunning: boolean;
  onGateReady?: (gate: string) => void;
}) {
  const [isRunning, setIsRunning] = useState(false);
  const [steps, setSteps] = useState<Set<string>>(new Set());
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Abort an in-flight stream if the component unmounts mid-run.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  async function handleRun() {
    setIsRunning(true);
    setSteps(new Set());
    setStatusLine("started");
    setRunError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await runAnalysis(matterId, {
        signal: controller.signal,
        onEvent: (frame: SseFrame) => {
          const data = (frame.data ?? {}) as Record<string, unknown>;
          if (frame.event === "status") {
            const state = String(data.state ?? "");
            if (state === "step" && typeof data.step === "string") {
              setSteps((prev) => new Set(prev).add(data.step as string));
            } else if (state) {
              setStatusLine(state);
            }
          } else if (frame.event === "error") {
            setRunError(String(data.detail ?? data.error ?? "Analysis error."));
          } else if (frame.event === "gate_ready") {
            // ONLY a real gate_ready advances — surfaced to the parent, never advanced locally.
            onGateReady?.(String(data.gate ?? ""));
          }
        },
      });
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Analysis connection failed.");
    } finally {
      setIsRunning(false);
      abortRef.current = null;
    }
  }

  const title = analysisRunning ? "Run analysis" : "Analysis";
  const description = analysisRunning
    ? "This matter is waiting on the Brain-1 analysis. Run it to build the chronology, ledger, and risk flags."
    : "Re-run the Brain-1 analysis to rebuild the chronology, ledger, and risk flags from the current record set.";
  const buttonLabel = analysisRunning ? "Run analysis" : "Re-run analysis";

  return (
    <Card data-testid="analysis-banner">
      <CardHeader className="flex-row items-center justify-between">
        <div className="flex flex-col gap-1">
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <Button onClick={handleRun} disabled={isRunning} data-testid="run-analysis">
          {isRunning ? "Running…" : buttonLabel}
        </Button>
      </CardHeader>

      {(isRunning || steps.size > 0 || runError) && (
        <CardContent className="flex flex-col gap-2">
          {runError && (
            <p role="alert" data-testid="analysis-error" className="text-sm text-danger">
              {runError}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2" data-testid="analysis-steps">
            {ANALYSIS_STEPS.map((step) => {
              const done = steps.has(step);
              return (
                <Badge
                  key={step}
                  variant={done ? "success" : "secondary"}
                  data-step={step}
                  data-done={done}
                >
                  {step.replace(/_/g, " ")}
                  {done ? " ✓" : ""}
                </Badge>
              );
            })}
            {isRunning && (
              <span
                aria-hidden
                className="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent"
              />
            )}
            {statusLine && (
              <span className="text-xs text-ink-muted" data-testid="analysis-status">
                {statusLine}
              </span>
            )}
          </div>
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------------------
// (2) Chronology panel
// ---------------------------------------------------------------------------------------

/** The exactly-four closed overlay fields the inline editor writes. */
type OverlayField = "narrative_override" | "provider_display" | "facility_display" | "encounter_type";

const OVERLAY_FIELDS: OverlayField[] = [
  "narrative_override",
  "provider_display",
  "facility_display",
  "encounter_type",
];

const OVERLAY_FIELD_LABELS: Record<OverlayField, string> = {
  narrative_override: "Narrative",
  provider_display: "Provider",
  facility_display: "Facility",
  encounter_type: "Encounter type",
};

function ChronologyPanel({
  matterId,
  chronology,
}: {
  matterId: string;
  chronology: EvidenceReviewVM["chronology"];
}) {
  const [editingRow, setEditingRow] = useState<string | null>(null);

  return (
    <Card data-testid="chronology-panel">
      <CardHeader>
        <CardTitle>Chronology</CardTitle>
        <CardDescription>
          {chronology.rows.length} encounter(s)
          {chronology.conflicts > 0 || chronology.parked > 0 ? (
            <>
              {" · "}
              <span data-testid="chronology-overlay-counts">
                {chronology.conflicts} conflict, {chronology.parked} parked
              </span>
            </>
          ) : null}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {chronology.rows.length === 0 ? (
          <p className="text-sm text-ink-muted">No chronology rows yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm" data-testid="chronology-table">
              <thead>
                <tr className="border-b border-border text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-medium">DOS</th>
                  <th className="py-2 pr-3 font-medium">Provider</th>
                  <th className="py-2 pr-3 font-medium">Facility</th>
                  <th className="py-2 pr-3 font-medium">Type</th>
                  <th className="py-2 pr-3 font-medium">Narrative</th>
                  <th className="py-2 pr-3 font-medium">Overlay</th>
                  <th className="py-2 font-medium" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {chronology.rows.map((row) =>
                  editingRow === row.row_id ? (
                    <ChronologyEditRow
                      key={row.row_id}
                      matterId={matterId}
                      row={row}
                      onDone={() => setEditingRow(null)}
                    />
                  ) : (
                    <ChronologyDisplayRow
                      key={row.row_id}
                      row={row}
                      onEdit={() => setEditingRow(row.row_id)}
                    />
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Overlay badge for a chronology row — `conflict` / `parked_orphaned` get a review hint. */
function OverlayBadge({ status }: { status: ChronologyRow["overlay_status"] }) {
  if (status === null) return null;
  if (status === "conflict") {
    return (
      <Badge variant="warning" data-testid="overlay-conflict">
        conflict — review
      </Badge>
    );
  }
  if (status === "parked_orphaned") {
    return (
      <Badge variant="warning" data-testid="overlay-parked">
        parked — review
      </Badge>
    );
  }
  return <Badge variant="info">applied</Badge>;
}

function ChronologyDisplayRow({ row, onEdit }: { row: ChronologyRow; onEdit: () => void }) {
  const conflicted = row.overlay_status === "conflict";
  return (
    <tr data-testid="chronology-row" data-row-id={row.row_id}>
      <td className="py-2 pr-3 align-top font-mono text-xs">{row.date_of_service}</td>
      <td className="py-2 pr-3 align-top">{row.provider_display}</td>
      <td className="py-2 pr-3 align-top">{row.facility_display}</td>
      <td className="py-2 pr-3 align-top">{row.encounter_type}</td>
      <td className="py-2 pr-3 align-top text-ink-muted">
        {row.narrative}
        {conflicted && (
          <span className="mt-1 block text-xs text-warning-foreground" data-testid="conflict-hint">
            The base record changed under this overlay — review both values before relying on it.
          </span>
        )}
      </td>
      <td className="py-2 pr-3 align-top">
        <OverlayBadge status={row.overlay_status} />
      </td>
      <td className="py-2 align-top">
        <Button variant="ghost" size="sm" onClick={onEdit} data-testid="chronology-edit">
          Edit
        </Button>
      </td>
    </tr>
  );
}

function ChronologyEditRow({
  matterId,
  row,
  onDone,
}: {
  matterId: string;
  row: ChronologyRow;
  onDone: () => void;
}) {
  const overlay = useChronologyOverlay(matterId);
  const [fields, setFields] = useState<Record<OverlayField, string>>({
    narrative_override: row.narrative,
    provider_display: row.provider_display,
    facility_display: row.facility_display,
    encounter_type: row.encounter_type,
  });

  function set(field: OverlayField, value: string) {
    setFields((prev) => ({ ...prev, [field]: value }));
  }

  function save() {
    // The submit carries EXACTLY the closed overlay-vocabulary keys — nothing else.
    const edited: ChronologyOverlayBody["edited_fields"] = {
      narrative_override: fields.narrative_override,
      provider_display: fields.provider_display,
      facility_display: fields.facility_display,
      encounter_type: fields.encounter_type,
    };
    overlay.mutate(
      { encounterId: row.row_id, body: { edited_fields: edited } },
      { onSuccess: () => onDone() },
    );
  }

  return (
    <tr data-testid="chronology-edit-row" data-row-id={row.row_id}>
      <td colSpan={7} className="py-3">
        <div className="flex flex-col gap-3 rounded-md border border-border bg-surface-muted p-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {OVERLAY_FIELDS.map((field) => (
              <div key={field} className="flex flex-col gap-1">
                <Label htmlFor={`overlay-${row.row_id}-${field}`}>
                  {OVERLAY_FIELD_LABELS[field]}
                </Label>
                <Input
                  id={`overlay-${row.row_id}-${field}`}
                  value={fields[field]}
                  onChange={(e) => set(field, e.target.value)}
                  data-field={field}
                />
              </div>
            ))}
          </div>
          {overlay.isError && (
            <p role="alert" data-testid="overlay-error" className="text-sm text-danger">
              {errorText(overlay.error, "Could not save the overlay.")}
            </p>
          )}
          <div className="flex gap-2">
            <Button size="sm" onClick={save} disabled={overlay.isPending} data-testid="overlay-save">
              {overlay.isPending ? "Saving…" : "Save row"}
            </Button>
            <Button size="sm" variant="ghost" onClick={onDone} disabled={overlay.isPending}>
              Cancel
            </Button>
          </div>
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------------------
// (3) Ledger panel
// ---------------------------------------------------------------------------------------

const LEDGER_COLS: { key: keyof LedgerColumns; label: string }[] = [
  { key: "billed_cents", label: "Billed" },
  { key: "adjusted_cents", label: "Adjusted" },
  { key: "paid_cents", label: "Paid" },
  { key: "outstanding_cents", label: "Outstanding" },
];

function LedgerPanel({ matterId, ledger }: { matterId: string; ledger: LedgerVM | null }) {
  // The ledger the panel DISPLAYS: starts from the VM, replaced wholesale by a billing-edit
  // response (server-authoritative — the FE never recomputes a total).
  const [displayLedger, setDisplayLedger] = useState<LedgerVM | null>(ledger);
  const [editing, setEditing] = useState(false);

  // Keep the display in sync when a fresh envelope arrives (e.g. after a re-run).
  useEffect(() => {
    setDisplayLedger(ledger);
  }, [ledger]);

  if (displayLedger === null) {
    return (
      <Card data-testid="ledger-panel">
        <CardHeader>
          <CardTitle>Specials ledger</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-ink-muted" data-testid="ledger-unavailable">
            No ledger — the jurisdiction pack is unavailable.
          </p>
        </CardContent>
      </Card>
    );
  }

  const categories = Object.keys(displayLedger.by_category).sort();

  return (
    <Card data-testid="ledger-panel">
      <CardHeader className="flex-row items-center justify-between">
        <div className="flex flex-col gap-1">
          <CardTitle>Specials ledger</CardTitle>
          <CardDescription>All amounts in USD.</CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setEditing((v) => !v)}
          data-testid="ledger-edit-toggle"
        >
          {editing ? "Close line editor" : "Edit lines"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm" data-testid="ledger-table">
            <thead>
              <tr className="border-b border-border text-xs text-ink-muted">
                <th className="py-2 pr-3 font-medium">Category</th>
                {LEDGER_COLS.map((col) => (
                  <th key={col.key} className="py-2 pr-3 text-right font-medium">
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {categories.map((cat) => (
                <tr key={cat} data-testid="ledger-row" data-category={cat}>
                  <td className="py-2 pr-3">{labelOf(CATEGORY_LABELS, cat)}</td>
                  {LEDGER_COLS.map((col) => (
                    <td key={col.key} className="py-2 pr-3 text-right font-mono">
                      {centsToDollars(displayLedger.by_category[cat][col.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-border font-medium" data-testid="ledger-grand-total">
                <td className="py-2 pr-3">Grand total</td>
                {LEDGER_COLS.map((col) => (
                  <td key={col.key} className="py-2 pr-3 text-right font-mono">
                    {centsToDollars(displayLedger.grand_total[col.key])}
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>

        <div className="flex flex-col gap-1 text-sm">
          <p data-testid="demand-basis">
            <span className="text-ink-muted">Demand basis ({displayLedger.basis}): </span>
            <span className="font-mono font-medium">
              {centsToDollars(displayLedger.demand_basis_total_cents)}
            </span>
          </p>
          {(displayLedger.missing_paid_line_ids.length > 0 ||
            displayLedger.excluded_line_ids.length > 0) && (
            <p className="text-xs text-ink-muted" data-testid="ledger-gaps">
              {displayLedger.missing_paid_line_ids.length} line(s) missing a paid amount ·{" "}
              {displayLedger.excluded_line_ids.length} line(s) excluded from the basis.
            </p>
          )}
        </div>

        {editing && (
          <LedgerLineEditor
            matterId={matterId}
            onLedgerReplaced={(next) => setDisplayLedger(next)}
          />
        )}
      </CardContent>
    </Card>
  );
}

/** One staged edit's money fields (dollar strings, keyed like the wire body). */
type StagedMoney = Pick<BillingLineEdit, "billed" | "adjusted" | "paid" | "outstanding">;

function LedgerLineEditor({
  matterId,
  onLedgerReplaced,
}: {
  matterId: string;
  onLedgerReplaced: (ledger: LedgerVM) => void;
}) {
  const linesQuery = useBillingLines(matterId, true);
  const billingEdits = useBillingEdits(matterId);

  // Staged edits, keyed by billing_line_id. category + four money strings; only touched fields set.
  const [staged, setStaged] = useState<
    Record<string, { category?: LedgerCategory } & StagedMoney>
  >({});
  // Per-field client-side money parse errors (block the batch — never send a guess).
  const [moneyErrors, setMoneyErrors] = useState<Record<string, string>>({});

  function stageCategory(lineId: string, category: LedgerCategory) {
    setStaged((prev) => ({ ...prev, [lineId]: { ...prev[lineId], category } }));
  }

  function stageMoney(lineId: string, field: keyof StagedMoney, value: string) {
    setStaged((prev) => ({ ...prev, [lineId]: { ...prev[lineId], [field]: value } }));
  }

  function saveEdits() {
    // Client-side money validation (input validation, like the strategy card): a bad amount blocks
    // the batch inline. An empty string is legal (clears). Only touched fields are sent.
    const errors: Record<string, string> = {};
    const edits: BillingLineEdit[] = [];
    for (const [lineId, staging] of Object.entries(staged)) {
      const edit: BillingLineEdit = { billing_line_id: lineId };
      if (staging.category !== undefined) edit.category = staging.category;
      for (const field of ["billed", "adjusted", "paid", "outstanding"] as const) {
        const raw = staging[field];
        if (raw === undefined) continue;
        // Empty string clears — send it verbatim. A non-empty value must parse as money.
        if (raw.trim() !== "" && dollarsToCents(raw) === MONEY_PARSE_ERROR) {
          errors[`${lineId}.${field}`] = "Enter a valid dollar amount.";
        }
        edit[field] = raw;
      }
      // Skip a row that staged nothing beyond its id.
      if (Object.keys(edit).length > 1) edits.push(edit);
    }
    if (Object.keys(errors).length > 0) {
      setMoneyErrors(errors);
      return;
    }
    setMoneyErrors({});
    if (edits.length === 0) return;
    billingEdits.mutate(
      { edits },
      {
        onSuccess: (res) => {
          // Replace the displayed ledger from the SERVER total (invariant 10 — no client sum).
          onLedgerReplaced(res.ledger);
          setStaged({});
        },
      },
    );
  }

  const lines = linesQuery.data?.lines ?? [];
  // Surface a per-field 422 from the backend (invalid_money_string / unknown_billing_line).
  const submitError =
    billingEdits.error instanceof ApiError ? billingEdits.error.body : null;

  return (
    <div className="rounded-md border border-border p-3" data-testid="ledger-line-editor">
      {linesQuery.isLoading ? (
        <p className="text-sm text-ink-muted">Loading billing lines…</p>
      ) : linesQuery.isError ? (
        <p className="text-sm text-danger">
          {errorText(linesQuery.error, "Could not load billing lines.")}
        </p>
      ) : lines.length === 0 ? (
        <p className="text-sm text-ink-muted">No billing lines.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs" data-testid="billing-lines-table">
              <thead>
                <tr className="border-b border-border text-ink-muted">
                  <th className="py-2 pr-2 font-medium">Provider / DOS</th>
                  <th className="py-2 pr-2 font-medium">Category</th>
                  <th className="py-2 pr-2 font-medium">Billed</th>
                  <th className="py-2 pr-2 font-medium">Adjusted</th>
                  <th className="py-2 pr-2 font-medium">Paid</th>
                  <th className="py-2 pr-2 font-medium">Outstanding</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {lines.map((line) => (
                  <BillingLineRow
                    key={line.id}
                    line={line}
                    staged={staged[line.id]}
                    moneyErrors={moneyErrors}
                    onCategory={(c) => stageCategory(line.id, c)}
                    onMoney={(f, v) => stageMoney(line.id, f, v)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {submitError && (
            <p role="alert" data-testid="billing-submit-error" className="text-sm text-danger">
              {submitError.error === "invalid_money_string"
                ? "One or more amounts are not valid dollar values."
                : submitError.error === "unknown_billing_line"
                  ? "A billing line no longer exists — refresh and retry."
                  : String(submitError.detail ?? submitError.error ?? "Could not save edits.")}
            </p>
          )}

          <div>
            <Button
              size="sm"
              onClick={saveEdits}
              disabled={billingEdits.isPending}
              data-testid="billing-save"
            >
              {billingEdits.isPending ? "Saving…" : "Save edits"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function BillingLineRow({
  line,
  staged,
  moneyErrors,
  onCategory,
  onMoney,
}: {
  line: BillingLine;
  staged: ({ category?: LedgerCategory } & StagedMoney) | undefined;
  moneyErrors: Record<string, string>;
  onCategory: (category: LedgerCategory) => void;
  onMoney: (field: keyof StagedMoney, value: string) => void;
}) {
  const moneyFields: { field: keyof StagedMoney; cents: number | null }[] = [
    { field: "billed", cents: line.billed_cents },
    { field: "adjusted", cents: line.adjusted_cents },
    { field: "paid", cents: line.paid_cents },
    { field: "outstanding", cents: line.outstanding_cents },
  ];
  return (
    <tr data-testid="billing-line-row" data-line-id={line.id}>
      <td className="py-2 pr-2 align-top">
        <div className="flex flex-col">
          <span>{line.provider}</span>
          <span className="font-mono text-[0.65rem] text-ink-muted">{line.date_of_service}</span>
        </div>
      </td>
      <td className="py-2 pr-2 align-top">
        <select
          aria-label={`Category for ${line.provider}`}
          value={staged?.category ?? line.category}
          onChange={(e) => onCategory(e.target.value as LedgerCategory)}
          data-testid="billing-category"
          className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {LEDGER_CATEGORIES.map((cat) => (
            <option key={cat} value={cat}>
              {labelOf(CATEGORY_LABELS, cat)}
            </option>
          ))}
        </select>
      </td>
      {moneyFields.map(({ field, cents }) => {
        const key = `${line.id}.${field}`;
        const err = moneyErrors[key];
        return (
          <td key={field} className="py-2 pr-2 align-top">
            <Input
              aria-label={`${field} for ${line.provider}`}
              inputMode="decimal"
              defaultValue={centsToDollars(cents)}
              onChange={(e) => onMoney(field, e.target.value)}
              onBlur={(e) => onMoney(field, e.target.value)}
              data-testid={`billing-${field}`}
              aria-invalid={err ? true : undefined}
              className="h-8 w-24 text-xs"
            />
            {err && (
              <p role="alert" data-testid={`billing-${field}-error`} className="text-[0.65rem] text-danger">
                {err}
              </p>
            )}
          </td>
        );
      })}
    </tr>
  );
}

// ---------------------------------------------------------------------------------------
// (4) Risk-flags panel
// ---------------------------------------------------------------------------------------

const SEVERITY_BADGE: Record<string, BadgeProps["variant"]> = {
  high: "danger",
  medium: "warning",
  low: "secondary",
};

function RiskFlagsPanel({
  matterId,
  flags,
  roleAffordances,
}: {
  matterId: string;
  flags: RiskFlagVM[];
  roleAffordances: RoleAffordances;
}) {
  const sorted = useMemo(
    () =>
      [...flags].sort(
        (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3),
      ),
    [flags],
  );

  return (
    <Card data-testid="risk-flags-panel">
      <CardHeader>
        <CardTitle>Risk flags</CardTitle>
        <CardDescription>
          {flags.length} flag(s). High-severity flags require attorney sign-off before G2a approval.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {sorted.length === 0 ? (
          <p className="text-sm text-ink-muted">No risk flags.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {sorted.map((flag) => (
              <RiskFlagRow
                key={flag.id}
                matterId={matterId}
                flag={flag}
                canApprove={roleAffordances.can_approve}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RiskFlagRow({
  matterId,
  flag,
  canApprove,
}: {
  matterId: string;
  flag: RiskFlagVM;
  canApprove: boolean;
}) {
  const disposition = useFlagDisposition(matterId);
  const [choice, setChoice] = useState<FlagDisposition>(flag.disposition ?? "address_in_letter");
  const [rationale, setRationale] = useState<string>(flag.disposition_rationale ?? "");
  const [localError, setLocalError] = useState<string | null>(null);

  const isHigh = flag.severity === "high";
  const rationaleRequired = choice === "omit_with_rationale";

  function save() {
    // Client-side required-rationale validation (input validation — fires nothing when it fails).
    if (rationaleRequired && rationale.trim() === "") {
      setLocalError("A rationale is required to omit an adverse fact.");
      return;
    }
    setLocalError(null);
    const body =
      rationale.trim() === ""
        ? { disposition: choice }
        : { disposition: choice, rationale };
    disposition.mutate({ flagId: flag.id, body });
  }

  // Surface a typed 403 (role_forbidden) inline, verbatim from the body.
  const apiError = disposition.error instanceof ApiError ? disposition.error : null;
  const serverError =
    apiError && apiError.body.error === "role_forbidden"
      ? `High-severity dispositions require an attorney (you are ${roleFromBody(apiError.body)}).`
      : apiError
        ? String(apiError.body.detail ?? apiError.body.error ?? "Could not save.")
        : null;

  return (
    <li
      className="flex flex-col gap-2 rounded-md border border-border p-3"
      data-testid="risk-flag-row"
      data-severity={flag.severity}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={SEVERITY_BADGE[flag.severity] ?? "secondary"}>{flag.severity}</Badge>
        <span className="font-medium text-ink">{labelOf(FLAG_KIND_LABELS, flag.kind)}</span>
        <Badge variant="outline" data-testid="detector-chip">
          {labelOf(DETECTOR_LABELS, flag.detector)}
        </Badge>
        <span className="text-xs text-ink-muted" data-testid="anchors-count">
          {flag.anchors.length} anchor(s)
        </span>
        {isHigh && (
          <span className="text-xs font-medium text-danger" data-testid="signoff-required">
            attorney sign-off required
          </span>
        )}
      </div>

      <p className="text-sm text-ink-muted">{flag.detail}</p>

      {flag.disposition && (
        <p className="text-xs text-ink-muted" data-testid="current-disposition">
          Dispositioned: {DISPOSITION_LABELS[flag.disposition]}
          {flag.disposition_role ? ` by ${flag.disposition_role}` : ""}
          {flag.disposition_rationale ? ` — "${flag.disposition_rationale}"` : ""}
        </p>
      )}

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor={`disposition-${flag.id}`}>Disposition</Label>
            <select
              id={`disposition-${flag.id}`}
              value={choice}
              onChange={(e) => setChoice(e.target.value as FlagDisposition)}
              data-testid="disposition-select"
              className="rounded-md border border-border bg-surface px-2 py-1 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {FLAG_DISPOSITIONS.map((d) => (
                <option key={d} value={d}>
                  {DISPOSITION_LABELS[d]}
                </option>
              ))}
            </select>
          </div>
          {/* Blocked (non-attorney on a high flag) stays CLICKABLE — the server refuses inline. */}
          <Button
            size="sm"
            onClick={save}
            disabled={disposition.isPending}
            data-testid="disposition-save"
          >
            {disposition.isPending ? "Saving…" : "Save disposition"}
          </Button>
        </div>

        <div className="flex flex-col gap-1">
          <Label htmlFor={`rationale-${flag.id}`}>
            Rationale{rationaleRequired ? " (required to omit)" : " (optional)"}
          </Label>
          <textarea
            id={`rationale-${flag.id}`}
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={2}
            data-testid="rationale-input"
            className="w-full rounded-md border border-border bg-surface px-2 py-1 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </div>

        {!canApprove && isHigh && (
          <p className="text-xs text-ink-muted">
            You may record a disposition; the attorney sign-off happens at the confirm step.
          </p>
        )}
        {localError && (
          <p role="alert" data-testid="rationale-error" className="text-sm text-danger">
            {localError}
          </p>
        )}
        {serverError && (
          <p role="alert" data-testid="disposition-error" className="text-sm text-danger">
            {serverError}
          </p>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------------------
// (5) Exhibits panel
// ---------------------------------------------------------------------------------------

const PHI_BADGE: Record<PhiDisposition, BadgeProps["variant"]> = {
  pending: "warning",
  cleared: "success",
  excluded: "secondary",
};

function ExhibitsPanel({
  matterId,
  exhibits,
}: {
  matterId: string;
  exhibits: EvidenceReviewVM["exhibits"];
}) {
  const [manifest, setManifest] = useState<ManifestResponse | null>(null);
  const [manifestError, setManifestError] = useState<string | null>(null);
  const [minting, setMinting] = useState(false);

  async function mintTokens() {
    setMinting(true);
    setManifestError(null);
    try {
      setManifest(await getManifest(matterId, true));
    } catch (error) {
      setManifestError(errorText(error, "Could not mint the manifest."));
    } finally {
      setMinting(false);
    }
  }

  // Prefer freshly-minted manifest entries (they carry token ids) over the VM's un-minted ones.
  const entries = manifest?.entries ?? exhibits.entries;
  const blocking = manifest?.blocking ?? exhibits.blocking;

  return (
    <Card data-testid="exhibits-panel">
      <CardHeader className="flex-row items-center justify-between">
        <div className="flex flex-col gap-1">
          <CardTitle>Exhibits</CardTitle>
          <CardDescription>
            Page-level include / exclude per document, PHI disposition, and the binder collation
            order.
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={mintTokens}
          disabled={minting}
          data-testid="mint-tokens"
        >
          {minting ? "Minting…" : "Mint exhibit tokens"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {blocking.length > 0 && (
          <div
            role="alert"
            data-testid="exhibits-blocking"
            className="rounded-md border border-danger/50 bg-danger/10 p-3 text-sm text-danger"
          >
            <p className="mb-1 font-medium">The binder is blocked:</p>
            <ul className="list-inside list-disc">
              {blocking.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          </div>
        )}
        {manifestError && (
          <p role="alert" className="text-sm text-danger">
            {manifestError}
          </p>
        )}

        {entries.length === 0 ? (
          <p className="text-sm text-ink-muted">No exhibits yet.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {entries.map((entry) => (
              <ExhibitRow key={entry.document_id} matterId={matterId} entry={entry} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ExhibitRow({ matterId, entry }: { matterId: string; entry: ExhibitEntry }) {
  const pick = useExhibitPick(matterId);
  const phi = usePhiDisposition(matterId);

  const [includeStr, setIncludeStr] = useState(() => formatPageRanges(entry.included_pages));
  const [excludeStr, setExcludeStr] = useState(() => formatPageRanges(entry.excluded_pages));
  const [sortOrder, setSortOrder] = useState(String(entry.sort_order));
  const [rangeError, setRangeError] = useState<string | null>(null);

  function save() {
    // Strict page-range parse — an invalid range blocks the submit inline (never send a guess).
    const include = parsePageRanges(includeStr, entry.page_count);
    if (include instanceof PageRangeError) {
      setRangeError(`Include: ${include.message}`);
      return;
    }
    const exclude = parsePageRanges(excludeStr, entry.page_count);
    if (exclude instanceof PageRangeError) {
      setRangeError(`Exclude: ${exclude.message}`);
      return;
    }
    const order = Number(sortOrder);
    if (!Number.isFinite(order) || !Number.isInteger(order)) {
      setRangeError("Sort order must be a whole number.");
      return;
    }
    setRangeError(null);
    pick.mutate({
      document_id: entry.document_id,
      include_pages: include,
      excluded_pages: exclude,
      sort_order: order,
    });
  }

  // The PHI endpoint is keyed by exhibit id, but the manifest entry (the pinned VM shape) exposes
  // only document_id — never the exhibit's DB id. The exhibit-pick PUT response DOES carry `id`, so
  // we learn it there: PHI actions are enabled once a pick this session returns the exhibit id.
  // Until then the PHI buttons stay clickable and surface an honest inline reason (no wrong-id fire,
  // no gray-out for a legal block — this is a data-availability gate, not a legal one).
  const exhibitId = pick.data?.id ?? null;

  const pickError = pick.error instanceof ApiError ? pick.error : null;
  const pickErrorText = pickError
    ? pickError.body.error === "invalid_pick"
      ? String(pickError.body.detail ?? "Invalid page selection.")
      : pickError.body.error === "gate_state_mismatch"
        ? "This gate changed — refresh and retry."
        : String(pickError.body.detail ?? pickError.body.error ?? "Could not save the pick.")
    : null;

  const phiError = phi.error instanceof ApiError ? phi.error : null;
  const phiErrorText = phiError
    ? phiError.body.error === "role_forbidden"
      ? `Clearing PHI requires an attorney (you are ${roleFromBody(phiError.body)}).`
      : String(phiError.body.detail ?? phiError.body.error ?? "Could not set PHI.")
    : null;

  const [phiNeedsPick, setPhiNeedsPick] = useState(false);

  function setPhi(disposition: PhiDisposition & ("cleared" | "excluded")) {
    if (exhibitId === null) {
      // The exhibit id isn't known yet this session — prompt a pick save first (honest inline).
      setPhiNeedsPick(true);
      return;
    }
    setPhiNeedsPick(false);
    phi.mutate({ exhibitId, body: { disposition } });
  }

  return (
    <li
      className="flex flex-col gap-2 rounded-md border border-border p-3"
      data-testid="exhibit-row"
      data-document-id={entry.document_id}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{entry.filename}</span>
        <span className="text-xs text-ink-muted">{entry.page_count} page(s)</span>
        <Badge variant={PHI_BADGE[entry.phi_disposition]} data-testid="phi-chip">
          PHI: {entry.phi_disposition}
        </Badge>
        {entry.exhibit_token_id && (
          <Badge variant="info" data-testid="exhibit-token">
            {entry.exhibit_token_id}
          </Badge>
        )}
        <span className="text-xs text-ink-muted">integrity: {entry.integrity}</span>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor={`include-${entry.document_id}`}>Include pages</Label>
          <Input
            id={`include-${entry.document_id}`}
            value={includeStr}
            onChange={(e) => setIncludeStr(e.target.value)}
            placeholder="e.g. 1-4,7"
            data-testid="include-pages"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`exclude-${entry.document_id}`}>Exclude pages</Label>
          <Input
            id={`exclude-${entry.document_id}`}
            value={excludeStr}
            onChange={(e) => setExcludeStr(e.target.value)}
            placeholder="e.g. 5"
            data-testid="exclude-pages"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`order-${entry.document_id}`}>Sort order</Label>
          <Input
            id={`order-${entry.document_id}`}
            inputMode="numeric"
            value={sortOrder}
            onChange={(e) => setSortOrder(e.target.value)}
            data-testid="sort-order"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={save} disabled={pick.isPending} data-testid="exhibit-save">
          {pick.isPending ? "Saving…" : "Save exhibit"}
        </Button>
        {/* PHI buttons stay clickable; a non-attorney gets the typed 403 inline. */}
        <Button
          size="sm"
          variant="outline"
          disabled={phi.isPending}
          onClick={() => setPhi("cleared")}
          data-testid="phi-clear"
        >
          Clear PHI
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={phi.isPending}
          onClick={() => setPhi("excluded")}
          data-testid="phi-exclude"
        >
          Exclude PHI
        </Button>
      </div>

      {phiNeedsPick && (
        <p role="alert" data-testid="phi-needs-pick" className="text-sm text-ink-muted">
          Save the exhibit first — the PHI disposition targets the saved exhibit.
        </p>
      )}

      {rangeError && (
        <p role="alert" data-testid="range-error" className="text-sm text-danger">
          {rangeError}
        </p>
      )}
      {pickErrorText && (
        <p role="alert" data-testid="pick-error" className="text-sm text-danger">
          {pickErrorText}
        </p>
      )}
      {phiErrorText && (
        <p role="alert" data-testid="phi-error" className="text-sm text-danger">
          {phiErrorText}
        </p>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------------------
// (6) Confirm bar
// ---------------------------------------------------------------------------------------

function ConfirmBar({
  matterId,
  payloadVersion,
  dedupPending,
}: {
  matterId: string;
  payloadVersion: number;
  dedupPending: number;
}) {
  const submit = useSubmitGate(matterId);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideError, setOverrideError] = useState<string | null>(null);

  function approve() {
    // ALWAYS fires — the server is the authority. A high open flag comes back 409 override_required.
    submit.mutate(
      { gate: "evidence_review", body: { action: "approve", payload_version: payloadVersion } },
      {
        onError: (error) => {
          if (
            error instanceof ApiError &&
            error.body.error === "override_required"
          ) {
            setOverrideOpen(true);
          }
        },
      },
    );
  }

  function submitOverride() {
    if (overrideReason.trim() === "") {
      setOverrideError("An override reason is required.");
      return;
    }
    setOverrideError(null);
    submit.mutate(
      {
        gate: "evidence_review",
        body: { action: "override", payload_version: payloadVersion, override_reason: overrideReason },
      },
      { onSuccess: () => setOverrideOpen(false) },
    );
  }

  // The inline confirm-bar error (non-override-required refusals render verbatim).
  const submitError = submit.error ?? null;
  const isOverrideRequired =
    submitError instanceof ApiError && submitError.body.error === "override_required";

  return (
    <Card data-testid="confirm-bar">
      <CardContent className="flex flex-col gap-3 pt-4">
        {dedupPending > 0 && (
          <a
            href="#documents"
            data-testid="dedup-advisory"
            className="inline-flex w-fit items-center rounded-full border border-warning/40 bg-warning/10 px-3 py-1 text-xs font-medium text-warning-foreground"
          >
            {dedupPending} duplicate decision(s) still pending — resolve in the documents panel.
          </a>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={approve} disabled={submit.isPending} data-testid="confirm-evidence">
            Confirm evidence (G2a)
          </Button>
        </div>

        {submitError && !isOverrideRequired && !overrideOpen && (
          <p role="alert" data-testid="confirm-error" className="text-sm text-danger">
            {confirmErrorText(submitError)}
          </p>
        )}

        {overrideOpen && (
          <div
            className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3"
            data-testid="override-dialog"
          >
            <p className="text-sm font-medium text-warning-foreground">
              A high-severity risk flag is still open. Confirming requires an override reason.
            </p>
            <textarea
              aria-label="Override reason"
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              rows={2}
              data-testid="override-reason"
              className="w-full rounded-md border border-border bg-surface px-2 py-1 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            {overrideError && (
              <p role="alert" data-testid="override-error" className="text-sm text-danger">
                {overrideError}
              </p>
            )}
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={submitOverride}
                disabled={submit.isPending}
                data-testid="override-submit"
              >
                {submit.isPending ? "Submitting…" : "Confirm with override"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setOverrideOpen(false)}
                disabled={submit.isPending}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------------------

/** Pull the actor role from a typed role_forbidden body (`actual`), defaulting to a neutral noun. */
function roleFromBody(body: Record<string, unknown>): string {
  const actual = body.actual;
  return typeof actual === "string" ? actual : "not an attorney";
}

/** Render an unknown error, preferring a typed ApiError body, else a fallback. */
function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    return String(error.body.detail ?? error.body.error ?? fallback);
  }
  if (error instanceof Error) {
    return error.message;
  }
  return fallback;
}

/** Confirm-bar refusal copy (verbatim-derived; the override path is handled by the dialog). */
function confirmErrorText(error: ApiError | GateStaleError): string {
  if (error instanceof GateStaleError) {
    return error.message;
  }
  return String(error.body.detail ?? error.body.error ?? "Could not confirm. Please try again.");
}
