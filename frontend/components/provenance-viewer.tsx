"use client";

/**
 * ProvenanceViewer — the M6 slide-over that answers "where did this come from?". A fixed
 * right-hand panel (house Card styling) over a dim backdrop, dismissible via the close button,
 * the backdrop, or Escape. This is UI chrome (a viewer), NOT a legal gate — closing it blocks
 * nothing, so the close affordances are ordinary (no "stay clickable / inline reason" rule here).
 *
 * Two source modes:
 *   - TOKEN mode (`{kind: "token", tokenId}`): lazily fetches provenance ON OPEN (never on mount),
 *     shows the display form + an outcome badge (unverified → amber "pending verification",
 *     disputed → red, amt_mismatch → red "ledger drift", ok → green) + a source chip
 *     (extractor/attorney/rules), then the anchor list (doc + page rows; a superseded anchor is
 *     badged red "superseded source"). Selecting an anchor renders {@link PdfPageView}.
 *     An AMT token carries a `composition` block instead of anchors (a computed sum lives on no
 *     page): the viewer renders the billing lines behind the figure — provider · date · amount
 *     headings over each line's own bill-page anchor — so a total is one click from its bills.
 *   - ANCHORS mode (`{kind: "anchors", anchors, label}`): NO provenance hop — the caller already
 *     holds the anchors (chronology rows / risk flags). They are normalized (blob_url via the ONE
 *     sanctioned helper in lib/provenance) and rendered directly.
 *
 * Design rules honored: nothing token-shaped renders (the bare `token_id` is an inert label, not
 * detokenized); page-LEVEL highlight only (delegated to PdfPageView); backend state is displayed,
 * never invented (a token refusal renders `body.error` verbatim).
 */

import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  toProvenanceAnchor,
  useProvenance,
  type AmtCompositionView,
  type AnchorLike,
  type CompositionLineView,
  type ProvenanceAnchor,
  type ProvenanceOutcome,
  type ProvenanceResponse,
  type ProvenanceSource,
} from "@/lib/provenance";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PdfPageView } from "@/components/pdf-page-view";

// ---------------------------------------------------------------------------------------
// Source discriminated union — what the panel was opened to show.
// ---------------------------------------------------------------------------------------

/** Open on a single registry token (bare id) — provenance is fetched lazily on open. */
export interface TokenSource {
  kind: "token";
  /** The BARE registry id (e.g. "FACT_3"). Used as a path segment + inert label; never detokenized. */
  tokenId: string;
}

/** Open on a caller-held anchor list (chronology / flags) — no provenance fetch. */
export interface AnchorsSource {
  kind: "anchors";
  anchors: (ProvenanceAnchor | AnchorLike)[];
  /** A human label for the panel header (e.g. "Dr. Ramos · 2025-02-01"). */
  label: string;
}

export type ProvenanceSourceInput = TokenSource | AnchorsSource;

export interface ProvenanceViewerProps {
  matterId: string;
  open: boolean;
  onClose: () => void;
  source: ProvenanceSourceInput;
}

// ---------------------------------------------------------------------------------------
// Badge mappings for the outcome + source chips.
// ---------------------------------------------------------------------------------------

const OUTCOME_BADGE: Record<ProvenanceOutcome, { variant: BadgeProps["variant"]; label: string }> = {
  ok: { variant: "success", label: "verified" },
  unverified: { variant: "warning", label: "pending verification" },
  disputed: { variant: "danger", label: "disputed" },
  amt_mismatch: { variant: "danger", label: "ledger drift" },
};

const SOURCE_LABEL: Record<ProvenanceSource, string> = {
  extractor: "extractor",
  attorney: "attorney",
  rules: "rules",
};

// ---------------------------------------------------------------------------------------
// Root — the slide-over shell (backdrop + panel + Escape/close), dispatching by source mode.
// ---------------------------------------------------------------------------------------

export function ProvenanceViewer({ matterId, open, onClose, source }: ProvenanceViewerProps) {
  // Escape closes the panel (UI chrome). Bound only while open.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="provenance-viewer" role="dialog" aria-modal="true" aria-label="Source provenance">
      {/* Backdrop — click to dismiss. */}
      <button
        type="button"
        aria-label="Close provenance viewer"
        onClick={onClose}
        data-testid="provenance-backdrop"
        className="absolute inset-0 bg-black/40"
      />

      {/* Panel — house Card styling; fixed width; scrolls its own body. */}
      <div className="relative flex h-full w-full max-w-xl flex-col border-l border-border bg-surface shadow-lg">
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-base font-semibold text-ink">Source provenance</h2>
          <Button variant="ghost" size="sm" onClick={onClose} data-testid="provenance-close">
            Close
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {source.kind === "token" ? (
            <TokenBody matterId={matterId} tokenId={source.tokenId} open={open} />
          ) : (
            <AnchorsBody anchors={source.anchors} label={source.label} />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Token mode — lazy provenance fetch, header (display form + outcome + source), anchor list.
// ---------------------------------------------------------------------------------------

function TokenBody({
  matterId,
  tokenId,
  open,
}: {
  matterId: string;
  tokenId: string;
  open: boolean;
}) {
  // Lazy: the query is enabled only while the panel is open with this token (fetch-on-open).
  const query = useProvenance(matterId, tokenId, open);

  if (query.isLoading || query.isPending) {
    return (
      <p data-testid="provenance-loading" className="text-sm text-ink-muted">
        Loading provenance…
      </p>
    );
  }

  if (query.isError || query.data === undefined) {
    const code =
      query.error instanceof ApiError
        ? String(query.error.body.error ?? query.error.body.detail ?? "Could not load provenance.")
        : "Could not load provenance.";
    return (
      <p role="alert" data-testid="provenance-error" className="text-sm text-danger">
        {code}
      </p>
    );
  }

  return <ResolvedProvenance provenance={query.data} />;
}

/** The token header + anchor list once provenance resolved. Anchors already carry `blob_url`. */
function ResolvedProvenance({ provenance }: { provenance: ProvenanceResponse }) {
  const outcome = OUTCOME_BADGE[provenance.outcome];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2" data-testid="provenance-header">
        <div className="flex flex-wrap items-center gap-2">
          {outcome && (
            <Badge variant={outcome.variant} data-testid="provenance-outcome" data-outcome={provenance.outcome}>
              {outcome.label}
            </Badge>
          )}
          <Badge variant="outline" data-testid="provenance-source" data-source={provenance.source}>
            {SOURCE_LABEL[provenance.source] ?? provenance.source}
          </Badge>
          {/* The bare token id — an inert audit label, never token-shaped copy to detokenize. */}
          <span className="text-xs text-ink-muted" data-testid="provenance-token-id">
            {provenance.token_id}
          </span>
        </div>
        <p className="text-sm font-medium text-ink" data-testid="provenance-display-form">
          {provenance.display_form}
        </p>
      </div>

      {provenance.composition ? (
        <CompositionList composition={provenance.composition} />
      ) : (
        <AnchorList items={provenance.anchors.map((anchor) => ({ anchor }))} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Composition — the billing lines behind a computed [[AMT]] figure (no page states the sum).
// ---------------------------------------------------------------------------------------

/** "Saguaro Regional Medical Center · 2025-03-14 · $9,200.00" — the per-line row heading. */
function lineHeading(line: CompositionLineView): string {
  const parts = [line.provider, line.date_of_service];
  if (line.amount !== null) parts.push(line.amount);
  return parts.join(" · ");
}

/**
 * An AMT's ledger composition: a summary line ("Computed from the billing ledger — ER billed ·
 * sum of 4 bill lines"), then the contributing lines as selectable anchor rows (each heading over
 * its own bill-page anchor). Ref ids that no longer resolve, and lines whose stored anchor names
 * no document, are surfaced — never silently dropped (backend state is displayed, not invented).
 */
function CompositionList({ composition }: { composition: AmtCompositionView }) {
  const linked = composition.lines.filter(
    (line): line is CompositionLineView & { anchor: ProvenanceAnchor } => line.anchor !== null,
  );
  const unlinked = composition.lines.filter((line) => line.anchor === null);
  return (
    <div className="flex flex-col gap-3" data-testid="composition">
      <p className="text-xs text-ink-muted" data-testid="composition-summary">
        Computed from the billing ledger
        {composition.hint ? ` — ${composition.hint}` : ""} · sum of {composition.lines.length} bill{" "}
        line{composition.lines.length === 1 ? "" : "s"}
      </p>
      {composition.missing_line_ids.length > 0 && (
        <p role="alert" className="text-xs text-danger" data-testid="composition-missing">
          {composition.missing_line_ids.length} ledger line
          {composition.missing_line_ids.length === 1 ? "" : "s"} in this figure no longer resolve
          {composition.missing_line_ids.length === 1 ? "s" : ""}.
        </p>
      )}
      <AnchorList
        items={linked.map((line) => ({ anchor: line.anchor, heading: lineHeading(line) }))}
      />
      {unlinked.length > 0 && (
        <ul className="flex flex-col gap-1" data-testid="composition-unlinked">
          {unlinked.map((line) => (
            <li key={line.line_id} className="text-xs text-ink-muted">
              {lineHeading(line)} — no source page linked
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Anchors mode — caller-held anchors (no provenance fetch); normalize + render directly.
// ---------------------------------------------------------------------------------------

function AnchorsBody({
  anchors,
  label,
}: {
  anchors: (ProvenanceAnchor | AnchorLike)[];
  label: string;
}) {
  // Normalize the lean wire anchors to full anchors — blob_url via the ONE sanctioned helper.
  const normalized = useMemo(() => anchors.map(toProvenanceAnchor), [anchors]);
  const items = useMemo(() => normalized.map((anchor) => ({ anchor })), [normalized]);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1" data-testid="provenance-header">
        <p className="text-sm font-medium text-ink" data-testid="provenance-display-form">
          {label}
        </p>
        <span className="text-xs text-ink-muted">
          {normalized.length} source page{normalized.length === 1 ? "" : "s"}
        </span>
      </div>
      <AnchorList items={items} />
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Shared: the selectable anchor list + the selected page render.
// ---------------------------------------------------------------------------------------

/** One selectable row: an anchor, optionally under a heading (a composition line's label). */
interface AnchorItem {
  anchor: ProvenanceAnchor;
  heading?: string | null;
}

function AnchorList({ items }: { items: AnchorItem[] }) {
  // Select the first anchor by default so a page is shown immediately when one exists.
  const [selected, setSelected] = useState<number>(0);

  // Keep the selection in range if the anchor set changes under us.
  useEffect(() => {
    setSelected((i) => (i < items.length ? i : 0));
  }, [items.length]);

  if (items.length === 0) {
    return (
      <p data-testid="provenance-no-anchors" className="text-sm text-ink-muted">
        No source pages are linked to this item.
      </p>
    );
  }

  const active = items[Math.min(selected, items.length - 1)].anchor;

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-2" data-testid="anchor-list">
        {items.map(({ anchor, heading }, i) => {
          const isActive = i === Math.min(selected, items.length - 1);
          return (
            <li key={`${anchor.document_id}:${anchor.page}:${i}`}>
              <button
                type="button"
                onClick={() => setSelected(i)}
                data-testid="anchor-row"
                data-document-id={anchor.document_id}
                data-page={anchor.page}
                data-active={isActive}
                className={
                  isActive
                    ? "flex w-full items-center justify-between gap-2 rounded-md border border-accent bg-surface-muted px-3 py-2 text-left text-sm"
                    : "flex w-full items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-surface-muted"
                }
              >
                <span className="flex flex-col">
                  {/* A composition line's heading (provider · date · amount) sits above the doc
                      label and takes over the emphasis; a plain anchor row keeps the doc name
                      as its lead line. */}
                  {heading && (
                    <span className="text-xs font-medium text-ink" data-testid="anchor-heading">
                      {heading}
                    </span>
                  )}
                  {/* Attorney-facing label: the document NAME (server-joined in token mode);
                      the bare id survives only as a data attribute + shortened fallback. */}
                  <span
                    className={heading ? "text-xs text-ink-muted" : "text-xs font-medium text-ink"}
                    data-testid="anchor-doc-label"
                  >
                    {anchor.filename ?? `Document ${anchor.document_id.slice(0, 8)}…`}
                  </span>
                  <span className="text-xs text-ink-muted">
                    page {anchor.page}
                    {anchor.page_count > 0 ? ` of ${anchor.page_count}` : ""}
                    {anchor.doc_type ? ` · ${anchor.doc_type.replaceAll("_", " ")}` : ""}
                  </span>
                </span>
                {anchor.superseded && (
                  <Badge variant="danger" data-testid="anchor-superseded">
                    superseded source
                  </Badge>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      {/* The selected anchor's page — page-level highlight is drawn by PdfPageView. */}
      <PdfPageView
        blobUrl={active.blob_url}
        page={active.page}
        pageCount={active.page_count}
        highlight
      />
    </div>
  );
}
