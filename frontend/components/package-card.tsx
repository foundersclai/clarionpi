"use client";

/**
 * PackageCard — the package_assembly / package_ready gate screens (one card, two shapes off the
 * {@link PackageVM} + `gate`).
 *
 * package_assembly: a `buildable` gate + "Build package" → the build SSE ({@link runPackageBuild}).
 * Artifact chips light up per `artifact_ready` frame; a `binder_blocked` ERROR frame renders its
 * reasons as a red banner (with the pending-PHI hint); a real `gate_ready {gate:"package_ready"}` is
 * surfaced to the parent, which refetches to advance the view. The card NEVER advances the gate.
 *
 * package_ready: the artifact-sets list (kind label, byte size, short sha, a same-origin Download
 * link per artifact — a plain `<a href>` the browser downloads natively) + an immutability note
 * ("new records start a fresh draft cycle").
 */

import { useEffect, useRef, useState } from "react";
import { runPackageBuild, useArtifacts } from "@/lib/drafting";
import type { ArtifactSetView, ArtifactView, GateState, PackageVM } from "@/lib/types";
import type { SseFrame } from "@/lib/sse";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export interface PackageCardProps {
  matterId: string;
  gate: GateState;
  vm: PackageVM;
  /** Called on a real `gate_ready` frame — the parent refetches to advance the view. */
  onGateReady?: (gate: string) => void;
}

/** The four artifact kinds a build emits — the fixed chip set, lit as each arrives. */
const ARTIFACT_KIND_LABELS: Record<string, string> = {
  letter_docx: "Demand letter (DOCX)",
  binder_pdf: "Exhibit binder (PDF)",
  chronology_xlsx: "Chronology (XLSX)",
  provenance_report: "Provenance report (PDF)",
};

const ARTIFACT_KIND_ORDER: readonly string[] = [
  "letter_docx",
  "binder_pdf",
  "chronology_xlsx",
  "provenance_report",
];

function kindLabel(kind: string): string {
  return ARTIFACT_KIND_LABELS[kind] ?? kind;
}

/** A short sha for display — first 12 hex chars (the full sha256 is on the wire, not shown). */
function shortSha(sha256: string): string {
  return sha256.slice(0, 12);
}

/** Bytes → a compact human size (KB/MB), for the artifact list. */
function humanBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function PackageCard({ matterId, gate, vm, onGateReady }: PackageCardProps) {
  const isReady = gate === "package_ready";

  // On package_ready, read the authoritative artifact sets (the VM already carries them, but the
  // query keeps the download list fresh across a refetch and is the single source for the list).
  const artifactsQuery = useArtifacts(matterId, isReady);
  const sets: ArtifactSetView[] = isReady
    ? (artifactsQuery.data?.sets ?? vm.artifact_sets)
    : vm.artifact_sets;

  return (
    <Card data-testid="package-card" data-gate={gate}>
      <CardHeader>
        <CardTitle>{isReady ? "Package ready" : "Package assembly"}</CardTitle>
        <CardDescription>
          {isReady
            ? "The demand package is built. Download the artifacts below."
            : "Assemble the demand package — the letter, the exhibit binder, the chronology, and the provenance report — from the approved draft."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {!isReady && (
          <BuildSection matterId={matterId} buildable={vm.buildable} onGateReady={onGateReady} />
        )}

        {isReady && <ArtifactSetsList sets={sets} />}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------
// package_assembly — the build SSE + progress + blocked banner.
// ---------------------------------------------------------------------------------------

function BuildSection({
  matterId,
  buildable,
  onGateReady,
}: {
  matterId: string;
  buildable: boolean;
  onGateReady?: (gate: string) => void;
}) {
  const [isRunning, setIsRunning] = useState(false);
  const [readyKinds, setReadyKinds] = useState<Set<string>>(new Set());
  const [blockedReasons, setBlockedReasons] = useState<string[] | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  async function handleBuild() {
    setIsRunning(true);
    setReadyKinds(new Set());
    setBlockedReasons(null);
    setRunError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await runPackageBuild(matterId, {
        signal: controller.signal,
        onEvent: (frame: SseFrame) => {
          const data = (frame.data ?? {}) as Record<string, unknown>;
          if (frame.event === "artifact_ready") {
            const kind = String(data.artifact_kind ?? "");
            setReadyKinds((prev) => new Set(prev).add(kind));
          } else if (frame.event === "error") {
            const code = String(data.error ?? "");
            if (code === "binder_blocked") {
              const reasons = Array.isArray(data.reasons) ? (data.reasons as string[]) : [];
              setBlockedReasons(reasons);
            } else {
              setRunError(buildErrorText(code, data));
            }
          } else if (frame.event === "gate_ready") {
            // ONLY a real gate_ready advances — surfaced to the parent, never advanced locally.
            onGateReady?.(String(data.gate ?? ""));
          }
        },
      });
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Build connection failed.");
    } finally {
      setIsRunning(false);
      abortRef.current = null;
    }
  }

  const showProgress = isRunning || readyKinds.size > 0;

  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button onClick={handleBuild} disabled={isRunning} data-testid="build-package">
          {isRunning ? "Building…" : "Build package"}
        </Button>
      </div>

      {!buildable && (
        <p className="text-xs text-ink-muted" data-testid="not-buildable-hint">
          The draft is not approved yet — building will refuse until the letter is approved at
          compliance review.
        </p>
      )}

      {blockedReasons !== null && (
        <Banner tone="error" heading="Package build blocked" data-testid="binder-blocked">
          <ul className="mb-2 flex list-inside list-disc flex-col gap-0.5" data-testid="binder-blocked-reasons">
            {blockedReasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
          <p className="text-xs">
            A common cause is a third-party-PHI exhibit still pending disposition — clear it at
            evidence review, then rebuild.
          </p>
        </Banner>
      )}

      {runError && (
        <p role="alert" data-testid="build-error" className="text-sm text-danger">
          {runError}
        </p>
      )}

      {showProgress && (
        <div className="flex flex-wrap items-center gap-2" data-testid="build-progress">
          {ARTIFACT_KIND_ORDER.map((kind) => {
            const done = readyKinds.has(kind);
            return (
              <Badge
                key={kind}
                variant={done ? "success" : "secondary"}
                data-artifact-kind={kind}
                data-done={done}
              >
                {kindLabel(kind)}
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
        </div>
      )}
    </div>
  );
}

/** Copy for a non-blocked build error frame (verbatim detail preferred). */
function buildErrorText(code: string, data: Record<string, unknown>): string {
  const detail = typeof data.detail === "string" ? data.detail : undefined;
  switch (code) {
    case "no_draft":
      return "There is no demand draft to package. Draft the letter first.";
    case "artifact_token_leak":
      return "An artifact contained an unresolved token and was blocked — this is a build defect; contact an administrator.";
    case "binder_page_missing":
      return "An exhibit page referenced by the binder is missing from the record. Re-check the exhibit picks.";
    default:
      return detail ?? (code || "Build error.");
  }
}

// ---------------------------------------------------------------------------------------
// package_ready — the artifact-sets list + downloads + immutability note.
// ---------------------------------------------------------------------------------------

function ArtifactSetsList({ sets }: { sets: ArtifactSetView[] }) {
  if (sets.length === 0) {
    return <p className="text-sm text-ink-muted">No artifact sets.</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-4" data-testid="artifact-sets">
        {sets.map((set) => (
          <li
            key={set.id}
            className="flex flex-col gap-2 rounded-md border border-border p-3"
            data-testid="artifact-set"
            data-set-id={set.id}
          >
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge variant="secondary">draft v{set.draft_version}</Badge>
              <Badge variant="secondary">registry v{set.registry_version}</Badge>
              {set.created_at && (
                <span className="text-xs text-ink-muted">built {set.created_at}</span>
              )}
            </div>
            <ul className="flex flex-col divide-y divide-border" data-testid="artifact-list">
              {set.artifacts.map((artifact) => (
                <ArtifactRow key={artifact.kind} artifact={artifact} />
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <p className="text-xs text-ink-muted" data-testid="immutability-note">
        These artifacts are immutable — new records start a fresh draft cycle rather than editing a
        built package.
      </p>
    </div>
  );
}

function ArtifactRow({ artifact }: { artifact: ArtifactView }) {
  return (
    <li
      className="flex flex-wrap items-center justify-between gap-2 py-2"
      data-testid="artifact-row"
      data-artifact-kind={artifact.kind}
    >
      <div className="flex flex-col">
        <span className="text-sm font-medium text-ink">{kindLabel(artifact.kind)}</span>
        <span className="text-xs text-ink-muted">
          {humanBytes(artifact.byte_count)} ·{" "}
          <span className="font-mono" data-testid="artifact-sha">
            {shortSha(artifact.sha256)}
          </span>
        </span>
      </div>
      {/* Same-origin GET — a plain anchor triggers the browser-native download. */}
      <a
        href={artifact.url}
        download
        data-testid="artifact-download"
        className="inline-flex h-8 items-center justify-center rounded-md border border-border bg-surface px-3 text-xs font-medium text-ink transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Download
      </a>
    </li>
  );
}
