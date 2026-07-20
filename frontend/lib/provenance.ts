"use client";

/**
 * Provenance client (M6 Wave B) — the token→provenance fetcher, its types, the ONE sanctioned
 * blob-URL constructor, and a LAZY provenance hook the viewer drives on demand.
 *
 * Wire disciplines carried here (binding):
 *   - The viewer NEVER constructs a document URL from parts. Token-mode anchors arrive with a
 *     server-sent `blob_url`; anchors-mode data (chronology rows / risk flags) carries no
 *     blob_url, so the ONE helper {@link blobUrlFor} maps a bare `document_id` to the KNOWN route
 *     shape `/api/documents/{id}/blob`. This mirrors how `lib/api.ts` centralizes paths — it is
 *     the FE's single point where a document URL is built, and nothing downstream re-derives one.
 *   - Nothing token-shaped renders. `token_id` is a BARE registry id (e.g. "FACT_3"); it is used
 *     as a path segment and shown as an inert label, never detokenized or reconstructed.
 *   - The provenance fetch is LAZY: {@link useProvenance} fires only when the viewer opens with a
 *     token (enabled-gated), never on mount, so no PHI-adjacent request rides an unopened panel.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ApiError, apiGet } from "@/lib/api";

// ---------------------------------------------------------------------------------------
// Types (mirror the PINNED backend contract for GET /api/matters/{id}/provenance/{tokenId}).
// ---------------------------------------------------------------------------------------

/**
 * The provenance outcome for a token — the verification stance the header badge reads.
 *   - `ok`            → verified (green);
 *   - `unverified`    → pending verification (amber);
 *   - `disputed`      → an attorney/rules conflict (red);
 *   - `amt_mismatch`  → the amount drifted from the ledger (red, "ledger drift").
 */
export type ProvenanceOutcome = "ok" | "unverified" | "disputed" | "amt_mismatch";

/** Where the token's value came from — surfaced as an inert source chip. */
export type ProvenanceSource = "extractor" | "attorney" | "rules";

/**
 * One provenance anchor — a page in a source document the token resolves to. `bbox` is ALWAYS
 * null at v1 (page-level highlight only; there is no in-page rectangle yet). `blob_url` is the
 * server-sent, same-origin document route the viewer loads verbatim; `superseded` marks an
 * anchor whose source page was replaced by a newer capture.
 */
export interface ProvenanceAnchor {
  document_id: string;
  page: number;
  /** ALWAYS null at v1 — page-LEVEL highlight only (no in-page rectangle is populated). */
  bbox: null;
  blob_url: string;
  page_count: number;
  /**
   * Server-joined document facts (token mode sends both) — the viewer labels a source page by
   * NAME ("01_police_report.pdf · page 2"), never a bare uuid. Absent/null on the lean
   * anchors-mode shape (chronology rows / risk flags) until those VMs are enriched too.
   */
  filename?: string | null;
  doc_type?: string | null;
  superseded: boolean;
}

/**
 * One billing line inside an AMT's ledger composition. `amount` is the SERVER-formatted display
 * figure for the AMT's column (null when the column has no figure for the line — e.g. missing
 * paid — or the pack basis was unresolvable; the FE never does money math). `anchor` is the
 * line's own server-enriched page anchor, or null when the stored anchor has no document.
 */
export interface CompositionLineView {
  line_id: string;
  provider: string;
  date_of_service: string;
  category: string;
  amount: string | null;
  anchor: ProvenanceAnchor | null;
}

/**
 * An AMT token's ledger composition — the billing lines its pinned `ledger_ref` sums over. A
 * computed figure has NO page of its own (anchors: []); this block is its provenance: each line
 * maps to a bill page. `hint` is the ledger-slot gloss ("ER billed", "demand basis");
 * `missing_line_ids` surfaces ref ids that no longer resolve (never silently dropped).
 */
export interface AmtCompositionView {
  column: string;
  hint: string | null;
  lines: CompositionLineView[];
  missing_line_ids: string[];
}

/** GET /api/matters/{id}/provenance/{tokenId} → the token's display form, outcome, source, anchors. */
export interface ProvenanceResponse {
  token_id: string;
  display_form: string;
  outcome: ProvenanceOutcome;
  source: ProvenanceSource;
  anchors: ProvenanceAnchor[];
  /** The AMT ledger composition — null/absent for non-ledger tokens. */
  composition?: AmtCompositionView | null;
}

/**
 * The minimal anchor shape the ANCHORS-mode surfaces (chronology rows, risk flags) already
 * carry on the wire: a `{document_id, page}` dict, with the rest of {@link ProvenanceAnchor}
 * absent. The viewer normalizes these to full anchors (blob_url via {@link blobUrlFor}).
 */
export interface AnchorLike {
  document_id: string;
  page: number;
  /** May be present on some payloads; ignored when absent (defensive). */
  page_count?: number;
  superseded?: boolean;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------------------
// The ONE sanctioned document-URL constructor.
// ---------------------------------------------------------------------------------------

/**
 * Build the same-origin blob route for a document id — the KNOWN pinned shape
 * `/api/documents/{id}/blob`. This is the FE's SINGLE sanctioned point for constructing a
 * document URL: token-mode uses the server-sent `blob_url` verbatim, and anchors-mode (whose
 * wire data carries no blob_url) routes through here so exactly one code path ever assembles a
 * document path — mirroring the central path discipline in `lib/api.ts`.
 */
export function blobUrlFor(documentId: string): string {
  return `/api/documents/${documentId}/blob`;
}

/**
 * Normalize an {@link AnchorLike} (or a full {@link ProvenanceAnchor}) to a full anchor the
 * viewer can render: `blob_url` is taken as-sent when present, else built via {@link blobUrlFor}
 * (anchors mode); `bbox` is pinned null (page-level highlight only); `page_count`/`superseded`
 * default when the lean wire shape omits them.
 */
export function toProvenanceAnchor(anchor: AnchorLike | ProvenanceAnchor): ProvenanceAnchor {
  const blobUrl =
    typeof (anchor as ProvenanceAnchor).blob_url === "string"
      ? (anchor as ProvenanceAnchor).blob_url
      : blobUrlFor(anchor.document_id);
  return {
    document_id: anchor.document_id,
    page: anchor.page,
    bbox: null,
    blob_url: blobUrl,
    page_count: typeof anchor.page_count === "number" ? anchor.page_count : 0,
    // Pass the server-joined document facts through when the wire carries them (token mode);
    // the lean anchors-mode shape omits them and the viewer falls back to a shortened doc id.
    filename: typeof anchor.filename === "string" ? anchor.filename : null,
    doc_type: typeof anchor.doc_type === "string" ? anchor.doc_type : null,
    superseded: anchor.superseded === true,
  };
}

// ---------------------------------------------------------------------------------------
// Fetcher + lazy hook.
// ---------------------------------------------------------------------------------------

/** Query key for a token's provenance under a matter. */
export const provenanceKey = (matterId: string, tokenId: string) =>
  ["provenance", matterId, tokenId] as const;

/**
 * GET the provenance for a bare token id (e.g. "FACT_3") under a matter. Throws {@link ApiError}
 * on a refusal — 404 `token_not_found`, 422 `invalid_token_id`. The viewer renders `body.error`
 * inline; the FE never invents an error string.
 */
export function getProvenance(matterId: string, tokenId: string): Promise<ProvenanceResponse> {
  return apiGet<ProvenanceResponse>(
    `/api/matters/${matterId}/provenance/${encodeURIComponent(tokenId)}`,
  );
}

/**
 * LAZY provenance query. `enabled` gates the fetch — the viewer passes `open && source is token`
 * so the request fires ONLY when the panel opens in token mode, never on mount. Keyed by
 * (matter, token) so re-opening the same token serves the cache; retry is left to the app
 * default (off in tests via the per-test client).
 */
export function useProvenance(
  matterId: string,
  tokenId: string | null,
  enabled: boolean,
): UseQueryResult<ProvenanceResponse, ApiError> {
  return useQuery<ProvenanceResponse, ApiError>({
    queryKey: provenanceKey(matterId, tokenId ?? ""),
    queryFn: () => getProvenance(matterId, tokenId as string),
    // Only fire when the caller has opened the panel with a real token.
    enabled: enabled && tokenId !== null && tokenId !== "",
  });
}
