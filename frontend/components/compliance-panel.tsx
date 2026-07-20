"use client";

/**
 * CompliancePanel — the G3 (compliance_review) gate screen.
 *
 * Top: the open-blocking counter + the mechanical/semantic bucket chips (the routing summary).
 * Below: the letter preview (sections by `sort_order`, each `rendered_preview` as paragraphs).
 * Spans are NOT interactive yet — M6 wires the click-through — but the span data (`span_id`,
 * `token_id`, `[start,end)`) is attached via `data-*` attributes so M6 has it. Nothing token-shaped
 * renders: the preview text is already token-resolved, and a chip shows only the BARE `token_id`.
 *
 * Findings list (blocking-first, from the wire): each finding shows its check-kind label, its
 * bucket/severity/status chips, the detail, and a section link. Actions are routed by bucket+status:
 *   - mechanical + open → "Re-render fix (patch)";
 *   - semantic + open → "Regenerate section" + "Accept w/ reason" + "Override w/ reason" (a reason
 *     dialog — required, client-validated non-blank before firing; everything else fires).
 * A HARD-BLOCK check kind gets an explanatory chip ("hard block — fix the underlying data") and its
 * patch button only when mechanical. 409/403/422 refusal bodies render inline verbatim.
 *
 * The G3 approve bar is ALWAYS clickable (no gray-out for a legal block); a `guard_failed`
 * `no_blocking_findings` refusal renders "N blocking findings remain".
 */

import { useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { GateStaleError, useSubmitGate } from "@/lib/gates";
import { useFindingAction } from "@/lib/drafting";
import {
  HARD_BLOCK_CHECK_KINDS,
  type CheckKind,
  type ComplianceFindingView,
  type ComplianceReviewVM,
  type ComplianceSectionView,
  type FindingActionBody,
  type RenderedSpanView,
  type RoleAffordances,
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
import { Label } from "@/components/ui/label";
import { ProvenanceViewer } from "@/components/provenance-viewer";

export interface CompliancePanelProps {
  matterId: string;
  vm: ComplianceReviewVM;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
}

const CHECK_KIND_LABELS: Record<string, string> = {
  orphan_token: "Orphan token",
  amt_ledger_mismatch: "Amount / ledger mismatch",
  dead_anchor: "Dead anchor",
  missing_exhibit: "Missing exhibit",
  missing_statutory_term: "Missing statutory term",
  undisposed_adverse: "Undisposed adverse fact",
  prose_total_mismatch: "Prose total mismatch",
  unsupported_causation: "Unsupported causation",
  strategy_drift: "Strategy drift",
  tone: "Tone",
};

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  patched: "Patched",
  regenerated: "Regenerated",
  re_verified: "Re-verified",
  dispositioned: "Dispositioned",
};

function checkKindLabel(kind: string): string {
  return CHECK_KIND_LABELS[kind] ?? kind;
}

function isHardBlock(kind: string): boolean {
  return HARD_BLOCK_CHECK_KINDS.has(kind as CheckKind);
}

export function CompliancePanel({
  matterId,
  vm,
  payloadVersion,
  roleAffordances,
}: CompliancePanelProps) {
  // M6 provenance click-through: an interactive letter span opens the viewer in token mode.
  const [viewerToken, setViewerToken] = useState<string | null>(null);

  if (vm.draft === null) {
    return (
      <Card data-testid="compliance-panel" data-no-draft="true">
        <CardHeader>
          <CardTitle>Compliance review (G3)</CardTitle>
          <CardDescription>
            No draft is available for review yet. Generate the demand letter first.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card data-testid="compliance-panel">
      <CardHeader>
        <CardTitle>Compliance review (G3)</CardTitle>
        <CardDescription>
          Review the drafted letter and clear the compliance findings. Blocking findings must be
          fixed or dispositioned before the letter can be approved.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        {/* Counters */}
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={vm.open_blocking > 0 ? "danger" : "success"}
            data-testid="open-blocking-count"
          >
            {vm.open_blocking} blocking open
          </Badge>
          <Badge variant="secondary" data-testid="bucket-mechanical">
            {vm.buckets.mechanical} mechanical
          </Badge>
          <Badge variant="secondary" data-testid="bucket-semantic">
            {vm.buckets.semantic} semantic
          </Badge>
        </div>

        {/* Letter preview — spans are click-through to the provenance viewer (token mode). */}
        <LetterPreview sections={vm.sections} onSpanClick={(tokenId) => setViewerToken(tokenId)} />

        {/* Findings (blocking-first, as the wire ordered them) */}
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium text-ink">Findings</p>
          {vm.findings.length === 0 ? (
            <p className="text-sm text-ink-muted">No compliance findings.</p>
          ) : (
            <ul className="flex flex-col gap-3" data-testid="findings-list">
              {vm.findings.map((finding) => (
                <FindingRow key={finding.id} matterId={matterId} finding={finding} />
              ))}
            </ul>
          )}
        </div>

        {/* G3 approve bar */}
        <ApproveBar
          matterId={matterId}
          payloadVersion={payloadVersion}
          roleAffordances={roleAffordances}
        />
      </CardContent>

      {/* M6 provenance viewer — opened by a letter-span click; token mode. */}
      <ProvenanceViewer
        matterId={matterId}
        open={viewerToken !== null}
        onClose={() => setViewerToken(null)}
        source={{ kind: "token", tokenId: viewerToken ?? "" }}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------------------
// Letter preview — sections by sort_order; spans attached via data-* for M6 (NOT interactive).
// ---------------------------------------------------------------------------------------

function LetterPreview({
  sections,
  onSpanClick,
}: {
  sections: ComplianceSectionView[];
  onSpanClick: (tokenId: string) => void;
}) {
  const ordered = useMemo(
    () => [...sections].sort((a, b) => a.sort_order - b.sort_order),
    [sections],
  );
  return (
    <div className="flex flex-col gap-4 rounded-md border border-border p-4" data-testid="letter-preview">
      {ordered.length === 0 ? (
        <p className="text-sm text-ink-muted">No sections drafted.</p>
      ) : (
        ordered.map((section) => (
          <section
            key={section.section_id}
            data-testid="preview-section"
            data-section-id={section.section_id}
            data-sort-order={section.sort_order}
            className="flex flex-col gap-1"
          >
            <h4 className="text-sm font-semibold text-ink">{section.section_id}</h4>
            <p className="whitespace-pre-wrap text-sm text-ink">
              <SpannedText
                text={section.rendered_preview ?? ""}
                spans={section.spans}
                onSpanClick={onSpanClick}
              />
            </p>
            {/* Span metadata for M6 click-through — kept off-screen alongside the interactive
                copy above (the hidden block preserves the existing data-* contract; the visible
                segments carry the same data-token-id and the click handler). */}
            {section.spans.length > 0 && (
              <div className="hidden" data-testid="preview-spans" aria-hidden>
                {section.spans.map((span) => (
                  <span
                    key={span.span_id}
                    data-span-id={span.span_id}
                    data-token-id={span.token_id}
                    data-start={span.start}
                    data-end={span.end}
                  />
                ))}
              </div>
            )}
          </section>
        ))
      )}
    </div>
  );
}

/**
 * Render a section's already-token-resolved preview text, turning each `[start, end)` span into a
 * clickable, subtly-underlined segment that opens the provenance viewer for its `token_id`. Spans
 * that fall out of range (defensive — the preview text is authoritative) are skipped; overlapping
 * spans are handled by walking left-to-right and ignoring any span that starts before the cursor.
 * Nothing token-shaped renders — the segment shows the plain preview substring, not the token id.
 */
function SpannedText({
  text,
  spans,
  onSpanClick,
}: {
  text: string;
  spans: RenderedSpanView[];
  onSpanClick: (tokenId: string) => void;
}) {
  const segments = useMemo(() => {
    const inRange = spans
      .filter((s) => s.start >= 0 && s.end <= text.length && s.start < s.end)
      .sort((a, b) => a.start - b.start);
    const out: Array<{ key: string; text: string; span: RenderedSpanView | null }> = [];
    let cursor = 0;
    for (const span of inRange) {
      // Skip a span that overlaps one already emitted (walk is strictly forward).
      if (span.start < cursor) continue;
      if (span.start > cursor) {
        out.push({ key: `plain-${cursor}`, text: text.slice(cursor, span.start), span: null });
      }
      out.push({ key: span.span_id, text: text.slice(span.start, span.end), span });
      cursor = span.end;
    }
    if (cursor < text.length) {
      out.push({ key: `plain-${cursor}`, text: text.slice(cursor), span: null });
    }
    return out;
  }, [text, spans]);

  return (
    <>
      {segments.map((seg) =>
        seg.span === null ? (
          seg.text
        ) : (
          <button
            key={seg.key}
            type="button"
            onClick={() => onSpanClick(seg.span!.token_id)}
            data-testid="preview-span"
            data-span-id={seg.span.span_id}
            data-token-id={seg.span.token_id}
            className="cursor-pointer rounded-sm underline decoration-dotted decoration-accent/60 underline-offset-2 hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {seg.text}
          </button>
        ),
      )}
    </>
  );
}

// ---------------------------------------------------------------------------------------
// One finding row — the chips, the detail, and the bucket/status-routed actions.
// ---------------------------------------------------------------------------------------

const SEVERITY_BADGE: Record<string, BadgeProps["variant"]> = {
  blocking: "danger",
  advisory: "warning",
};

function FindingRow({
  matterId,
  finding,
}: {
  matterId: string;
  finding: ComplianceFindingView;
}) {
  const action = useFindingAction(matterId);
  const [reasonMode, setReasonMode] = useState<"accept" | "override" | null>(null);
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const hardBlock = isHardBlock(finding.check_kind);
  const isOpen = finding.status === "open";
  const isMechanical = finding.bucket === "mechanical";
  const isSemantic = finding.bucket === "semantic";

  function fire(body: FindingActionBody) {
    setLocalError(null);
    action.mutate({ findingId: finding.id, body });
  }

  function submitReason() {
    if (reasonMode === null) return;
    // Client-side required-reason validation — fires nothing when blank.
    if (reason.trim() === "") {
      setLocalError("A reason is required.");
      return;
    }
    setLocalError(null);
    action.mutate(
      { findingId: finding.id, body: { action: reasonMode, override_reason: reason } },
      {
        onSuccess: () => {
          setReasonMode(null);
          setReason("");
        },
      },
    );
  }

  const serverError =
    action.error instanceof ApiError ? findingErrorText(action.error) : null;

  return (
    <li
      className="flex flex-col gap-2 rounded-md border border-border p-3"
      data-testid="finding-row"
      data-check-kind={finding.check_kind}
      data-bucket={finding.bucket}
      data-severity={finding.severity}
      data-status={finding.status}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{checkKindLabel(finding.check_kind)}</span>
        <Badge variant={SEVERITY_BADGE[finding.severity] ?? "secondary"}>{finding.severity}</Badge>
        <Badge variant="outline" data-testid="bucket-chip">
          {finding.bucket}
        </Badge>
        <Badge variant="secondary" data-testid="status-chip">
          {STATUS_LABELS[finding.status] ?? finding.status}
        </Badge>
        <span className="text-xs text-ink-muted" data-testid="finding-section-link">
          in {finding.section_id}
        </span>
        {hardBlock && (
          <span className="text-xs font-medium text-danger" data-testid="hard-block-chip">
            hard block — fix the underlying data
          </span>
        )}
      </div>

      <p className="text-sm text-ink-muted">{finding.detail}</p>

      {finding.disposition && (
        <p className="text-xs text-ink-muted" data-testid="finding-disposition">
          Dispositioned: {finding.disposition}
          {finding.override_reason ? ` — "${finding.override_reason}"` : ""}
        </p>
      )}

      {/* Actions — routed by bucket + status. A mechanical open finding (incl. a hard block) can be
          patched; a semantic open finding can be regenerated / accepted / overridden with a reason. */}
      {isOpen && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-2">
            {isMechanical && (
              <Button
                size="sm"
                onClick={() => fire({ action: "patch" })}
                disabled={action.isPending}
                data-testid="finding-patch"
              >
                {action.isPending ? "Working…" : "Re-render fix (patch)"}
              </Button>
            )}
            {isSemantic && (
              <>
                <Button
                  size="sm"
                  onClick={() => fire({ action: "regen" })}
                  disabled={action.isPending}
                  data-testid="finding-regen"
                >
                  {action.isPending ? "Working…" : "Regenerate section"}
                </Button>
                {/* Accept / override open a reason dialog; a hard block is not dispositionable to
                    ship, but the button stays CLICKABLE — the server refuses inline (inv: no gray-out). */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setReasonMode("accept");
                    setReason("");
                    setLocalError(null);
                  }}
                  disabled={action.isPending}
                  data-testid="finding-accept"
                >
                  Accept w/ reason
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setReasonMode("override");
                    setReason("");
                    setLocalError(null);
                  }}
                  disabled={action.isPending}
                  data-testid="finding-override"
                >
                  Override w/ reason
                </Button>
              </>
            )}
          </div>

          {reasonMode !== null && (
            <div className="flex flex-col gap-1" data-testid="reason-dialog">
              <Label htmlFor={`reason-${finding.id}`}>
                {reasonMode === "accept" ? "Reason to accept" : "Reason to override"} (required)
              </Label>
              <textarea
                id={`reason-${finding.id}`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                data-testid="reason-input"
                className="w-full rounded-md border border-border bg-surface px-2 py-1 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={submitReason}
                  disabled={action.isPending}
                  data-testid="reason-submit"
                >
                  {action.isPending ? "Saving…" : "Submit"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setReasonMode(null);
                    setReason("");
                    setLocalError(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {localError && (
        <p role="alert" data-testid="finding-local-error" className="text-xs text-danger">
          {localError}
        </p>
      )}
      {serverError && (
        <p role="alert" data-testid="finding-server-error" className="text-sm text-danger">
          {serverError}
        </p>
      )}
    </li>
  );
}

/** Copy for a finding-action refusal (verbatim body preferred). */
function findingErrorText(error: ApiError): string {
  if (error.body.error === "hard_block_not_disposable") {
    const kind = typeof error.body.check_kind === "string" ? error.body.check_kind : "";
    return `This is a hard block${kind ? ` (${checkKindLabel(kind)})` : ""} — it must be fixed at the underlying data, not dispositioned.`;
  }
  if (error.body.error === "role_forbidden") {
    const actual = Array.isArray(error.body.actual)
      ? String((error.body.actual as unknown[])[0] ?? "")
      : typeof error.body.actual === "string"
        ? error.body.actual
        : "";
    return actual
      ? `Dispositioning a finding requires an attorney. (You are signed in as ${actual}.)`
      : "Dispositioning a finding requires an attorney.";
  }
  if (error.body.error === "disposition_reason_required") {
    return "A reason is required to disposition this finding.";
  }
  if (error.body.error === "disposition_action_not_supported") {
    const act = typeof error.body.action === "string" ? error.body.action : "";
    return `That action is not supported for this finding${act ? ` (${act})` : ""}.`;
  }
  return error.body.detail ?? error.body.error ?? "Could not apply the action. Please try again.";
}

// ---------------------------------------------------------------------------------------
// G3 approve bar — ALWAYS clickable; guard_failed no_blocking_findings renders "N remain".
// ---------------------------------------------------------------------------------------

function ApproveBar({
  matterId,
  payloadVersion,
  roleAffordances,
}: {
  matterId: string;
  payloadVersion: number;
  roleAffordances: RoleAffordances;
}) {
  const submit = useSubmitGate(matterId);

  function approve() {
    // ALWAYS fires — the server is the authority (no client-side legal suppression).
    submit.mutate({
      gate: "compliance_review",
      body: { action: "approve", payload_version: payloadVersion },
    });
  }

  const submitError = submit.error ?? null;

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-4">
      <Button onClick={approve} disabled={submit.isPending} data-testid="approve-compliance">
        Approve letter &amp; assemble package
      </Button>

      {submitError && (
        <p role="alert" data-testid="compliance-submit-error" className="text-sm text-danger">
          {approveErrorText(submitError)}
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
    </div>
  );
}

/** Copy for a G3 approve refusal (verbatim body preferred). */
function approveErrorText(error: unknown): string {
  if (error instanceof GateStaleError) {
    return error.message;
  }
  // A fetch-layer reject (server down / network blip) has no `.body` — guard so it renders inline
  // rather than crashing the panel on `.body.error`.
  if (!(error instanceof ApiError)) {
    return "Could not reach the server to submit. Check your connection and try again.";
  }
  if (error.body.error === "role_forbidden") {
    const actor = actorRoleFrom(error.body.detail);
    return actor
      ? `Approving the letter requires an attorney. (You are signed in as ${actor}.)`
      : "Approving the letter requires an attorney.";
  }
  if (error.body.error === "guard_failed" && error.body.code === "no_blocking_findings") {
    // The detail carries the count; render it verbatim, else a generic remains-message.
    return error.body.detail ?? "Blocking findings remain — clear them before approving.";
  }
  return error.body.detail ?? error.body.error ?? "Could not submit. Please try again.";
}

function actorRoleFrom(detail: string | undefined): string | null {
  if (!detail) return null;
  const match = /actor role is (\w+)/i.exec(detail);
  return match ? match[1] : null;
}
