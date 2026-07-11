"use client";

/**
 * DocumentsPanel — the matter dashboard's corpus surface. Four jobs:
 *   1. List documents (GET /api/matters/{id}/documents).
 *   2. Upload a batch: register a session (POST .../uploads) → PUT each file to its
 *      slot upload_url → commit (POST /api/uploads/{sid}/commit) → refetch documents.
 *   3. Run Phase-0 ingest over SSE and render progress from `doc_state`/`status` frames.
 *      isRunning drives a spinner; ONLY a `gate_ready` frame (or the post-run refetch) is
 *      allowed to advance the gate — surfaced via `onGateReady`, never advanced locally.
 *   4. Dedup queue: list pending decisions and resolve them (kept / superseded).
 *
 * The "Run ingest" button stays clickable even with zero documents (a zero-doc run is a
 * legal backend operation) — no gray-out for a would-be-blocked action; the backend decides.
 */

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiPost, apiPutBytes } from "@/lib/api";
import type {
  DedupDecisionView,
  DedupListResponse,
  DedupResolution,
  DocumentListResponse,
  DocumentView,
  UploadSessionView,
  UploadSlotView,
} from "@/lib/types";
import { runIngest, type SseFrame } from "@/lib/sse";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export interface DocumentsPanelProps {
  matterId: string;
  /** Called when a `gate_ready` frame arrives — the parent refetches the matter to advance. */
  onGateReady?: (gate: string) => void;
}

const DOC_STATUS_VARIANT: Record<string, BadgeProps["variant"]> = {
  uploaded: "secondary",
  classified: "info",
  ocr_done: "info",
  extracted: "success",
  failed: "danger",
};

const documentsKey = (matterId: string) => ["documents", matterId] as const;
const dedupKey = (matterId: string) => ["dedup", matterId] as const;

// ---------------------------------------------------------------------------------------

export function DocumentsPanel({ matterId, onGateReady }: DocumentsPanelProps) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- Documents list -----------------------------------------------------------------
  const documentsQuery = useQuery({
    queryKey: documentsKey(matterId),
    queryFn: () =>
      apiGet<DocumentListResponse>(`/api/matters/${matterId}/documents`),
  });

  // --- Pending dedup decisions --------------------------------------------------------
  const dedupQuery = useQuery({
    queryKey: dedupKey(matterId),
    queryFn: () =>
      apiGet<DedupListResponse>(`/api/matters/${matterId}/dedup?pending_only=true`),
  });

  // --- Upload flow (register → PUT each → commit) -------------------------------------
  const [uploadError, setUploadError] = useState<string | null>(null);
  const uploadMutation = useMutation<DocumentView[], ApiError, File[]>({
    mutationFn: async (files) => {
      const session = await apiPost<UploadSessionView>(
        `/api/matters/${matterId}/uploads`,
        { files: files.map((f) => ({ filename: f.name, size_bytes: f.size })) },
      );
      // Pair browser files to slots by the backend's stable `ordinal` (registration
      // order) — NEVER by response-array index, which the backend does not guarantee.
      const fileByOrdinal = new Map(files.map((file, index) => [index, file] as const));
      const unmatched = session.slots.filter(
        (slot: UploadSlotView) => !fileByOrdinal.has(slot.ordinal),
      );
      if (unmatched.length > 0) {
        // Fail BEFORE any commit: a slot we cannot pair means bytes could land under the
        // wrong declared identity, which would corrupt the provenance spine.
        throw new ApiError(0, {
          error: "upload_slot_mismatch",
          detail: "Upload slots did not match the chosen files; nothing was committed.",
        });
      }
      // Pairing diagnostic (upload-safety audit SEC-05/BUS-06), debug-level: index/id/
      // boolean only, never raw filenames (PHI risk).
      session.slots.forEach((slot: UploadSlotView) => {
        const file = fileByOrdinal.get(slot.ordinal);
        console.debug("clarionpi.uploads.pairing", {
          browser_file_index: slot.ordinal,
          slot_id: slot.id,
          filename_matches: file !== undefined && file.name === slot.filename,
        });
      });
      await Promise.all(
        session.slots.map((slot: UploadSlotView) => {
          const file = fileByOrdinal.get(slot.ordinal);
          if (!slot.upload_url || !file) return Promise.resolve();
          return apiPutBytes<UploadSlotView>(slot.upload_url, file);
        }),
      );
      const committed = await apiPost<{ documents: DocumentView[] }>(
        `/api/uploads/${session.id}/commit`,
      );
      return committed.documents;
    },
    onSuccess: () => {
      setUploadError(null);
      void queryClient.invalidateQueries({ queryKey: documentsKey(matterId) });
    },
    onError: (error) => {
      setUploadError(error.body.error ?? error.body.detail ?? "Upload failed.");
    },
  });

  function onFilesChosen() {
    const input = fileInputRef.current;
    if (!input || !input.files || input.files.length === 0) return;
    uploadMutation.mutate(Array.from(input.files));
    input.value = ""; // allow re-choosing the same file
  }

  // --- Dedup resolve ------------------------------------------------------------------
  const resolveMutation = useMutation<
    unknown,
    ApiError,
    { decisionId: string; resolution: DedupResolution }
  >({
    mutationFn: ({ decisionId, resolution }) =>
      apiPost(`/api/dedup/${decisionId}/resolve`, { resolution }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: dedupKey(matterId) });
      void queryClient.invalidateQueries({ queryKey: documentsKey(matterId) });
    },
  });

  // --- Ingest SSE run -----------------------------------------------------------------
  const [isRunning, setIsRunning] = useState(false);
  const [progress, setProgress] = useState<string[]>([]);
  const [runError, setRunError] = useState<string | null>(null);

  function describeFrame(frame: SseFrame): string | null {
    const data = (frame.data ?? {}) as Record<string, unknown>;
    switch (frame.event) {
      case "status":
        return `status: ${String(data.state ?? "")}`;
      case "doc_state":
        return `doc ${shortId(data.document_id)}: ${String(data.status ?? "")}`;
      case "budget_warning":
        return "budget warning";
      case "error":
        return `error: ${String(data.error ?? data.detail ?? "")}`;
      default:
        return null; // section/gate_ready/artifact_ready handled elsewhere or not shown
    }
  }

  async function handleRunIngest() {
    setIsRunning(true);
    setProgress([]);
    setRunError(null);
    try {
      await runIngest(matterId, {
        onEvent: (frame) => {
          if (frame.event === "error") {
            const data = (frame.data ?? {}) as Record<string, unknown>;
            setRunError(String(data.detail ?? data.error ?? "Ingest error."));
          }
          // ONLY a real gate_ready frame advances the gate — never a local guess.
          if (frame.event === "gate_ready") {
            const data = (frame.data ?? {}) as Record<string, unknown>;
            onGateReady?.(String(data.gate ?? ""));
          }
          const line = describeFrame(frame);
          if (line) setProgress((prev) => [...prev, line]);
        },
      });
      // Post-run refetch: pull the authoritative document + dedup state the run committed.
      void queryClient.invalidateQueries({ queryKey: documentsKey(matterId) });
      void queryClient.invalidateQueries({ queryKey: dedupKey(matterId) });
    } catch (error) {
      setRunError(
        error instanceof Error ? error.message : "Ingest connection failed.",
      );
    } finally {
      setIsRunning(false);
    }
  }

  const documents = documentsQuery.data?.documents ?? [];
  const decisions = dedupQuery.data?.decisions ?? [];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Documents</CardTitle>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            aria-label="Choose files to upload"
            onChange={onFilesChosen}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={uploadMutation.isPending}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploadMutation.isPending ? "Uploading…" : "Upload files"}
          </Button>
          {/* Stays clickable at zero docs — a zero-doc run is the backend's call. */}
          <Button size="sm" disabled={isRunning} onClick={handleRunIngest}>
            {isRunning ? "Running ingest…" : "Run ingest"}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        {uploadError && (
          <p role="alert" className="text-sm text-danger">
            {uploadError}
          </p>
        )}
        {runError && (
          <p role="alert" data-testid="ingest-error" className="text-sm text-danger">
            {runError}
          </p>
        )}

        {/* Documents list */}
        {documentsQuery.isLoading ? (
          <p className="text-sm text-ink-muted">Loading documents…</p>
        ) : documentsQuery.isError ? (
          <p className="text-sm text-danger">
            {queryErrorText(documentsQuery.error, "Could not load documents.")}
          </p>
        ) : documents.length === 0 ? (
          <p className="text-sm text-ink-muted">
            No documents yet. Upload a batch, then run ingest.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-border" data-testid="document-list">
            {documents.map((doc) => (
              <li key={doc.id} className="flex items-center justify-between py-2">
                <div className="flex flex-col">
                  <span className="text-sm text-ink">{doc.filename}</span>
                  <span className="text-xs text-ink-muted">
                    {doc.doc_type} · {doc.page_count} page{doc.page_count === 1 ? "" : "s"}
                    {doc.needs_review ? " · needs review" : ""}
                  </span>
                </div>
                <Badge variant={DOC_STATUS_VARIANT[doc.status] ?? "secondary"}>
                  {doc.status}
                </Badge>
              </li>
            ))}
          </ul>
        )}

        {/* Ingest progress */}
        {(isRunning || progress.length > 0) && (
          <div
            className="rounded-md border border-border bg-surface-muted p-3"
            data-testid="ingest-progress"
          >
            <div className="mb-1 flex items-center gap-2">
              {isRunning && (
                <span
                  aria-hidden
                  className="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent"
                />
              )}
              <span className="text-xs font-medium text-ink-muted">
                {isRunning ? "Ingest running" : "Last ingest run"}
              </span>
            </div>
            <ol className="flex flex-col gap-0.5 font-mono text-xs text-ink-muted">
              {progress.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ol>
          </div>
        )}

        {/* Dedup queue */}
        {decisions.length > 0 && (
          <DedupQueue
            decisions={decisions}
            pending={resolveMutation.isPending}
            onResolve={(decisionId, resolution) =>
              resolveMutation.mutate({ decisionId, resolution })
            }
          />
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------

function DedupQueue({
  decisions,
  pending,
  onResolve,
}: {
  decisions: DedupDecisionView[];
  pending: boolean;
  onResolve: (decisionId: string, resolution: DedupResolution) => void;
}) {
  return (
    <div data-testid="dedup-queue">
      <p className="mb-2 text-sm font-medium text-ink">
        Duplicate review ({decisions.length} pending)
      </p>
      <ul className="flex flex-col gap-2">
        {decisions.map((decision) => (
          <li
            key={decision.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border p-2"
          >
            <span className="text-xs text-ink-muted">
              Doc {shortId(decision.document_id)} vs{" "}
              {decision.against_document_id
                ? shortId(decision.against_document_id)
                : "—"}{" "}
              · {decision.status}
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={pending}
                onClick={() => onResolve(decision.id, "kept")}
              >
                Keep
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={pending}
                onClick={() => onResolve(decision.id, "superseded")}
              >
                Supersede
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

/** First 8 chars of a UUID, for compact display. Accepts unknown (frame payloads). */
function shortId(value: unknown): string {
  return typeof value === "string" ? value.slice(0, 8) : "?";
}

/** Render a query error, preferring a typed refusal code. */
function queryErrorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    return error.body.error ?? error.body.detail ?? fallback;
  }
  return fallback;
}
