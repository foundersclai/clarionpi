"use client";

/**
 * MattersList — the home-page matter list, now backed by the real GET /api/matters (the
 * backend gained a firm-scoped list endpoint at M3). This REPLACES the M2 localStorage
 * stopgap: the list is authoritative backend state (tenant-scoped, newest first), so each
 * row can honestly show the matter's current gate_state. Rendered from the fetched
 * MatterView, never from a client-remembered guess.
 *
 * The component export stays `RecentMattersList` so the home page composition is unchanged.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiGet } from "@/lib/api";
import { GATE_STATE_LABELS, type MatterListResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function RecentMattersList() {
  const query = useQuery<MatterListResponse, ApiError>({
    queryKey: ["matters"],
    queryFn: () => apiGet<MatterListResponse>("/api/matters"),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Matters</CardTitle>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <p className="text-sm text-ink-muted">Loading matters…</p>
        ) : query.isError ? (
          <p className="text-sm text-danger" data-testid="matters-error">
            {query.error instanceof ApiError
              ? (query.error.body.error ?? query.error.body.detail ?? "Could not load matters.")
              : "Could not load matters."}
          </p>
        ) : (query.data?.matters.length ?? 0) === 0 ? (
          <p className="text-sm text-ink-muted">
            No matters yet. Create one to get started.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-border" data-testid="matters-list">
            {query.data?.matters.map((matter) => (
              <li key={matter.id} className="flex items-center justify-between gap-2 py-2">
                <div className="flex flex-col">
                  <Link
                    href={`/matters/${matter.id}`}
                    className="text-sm font-medium text-accent hover:underline"
                  >
                    {matter.client_display_name}
                  </Link>
                  <span className="text-xs text-ink-muted">
                    {matter.claim_type.toUpperCase()} · {matter.jurisdiction} · incident{" "}
                    {matter.incident_date}
                  </span>
                </div>
                <Badge variant="secondary">{GATE_STATE_LABELS[matter.gate_state]}</Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
