"use client";

/**
 * Matter dashboard shell. Fetches the authoritative MatterView and renders:
 *   (a) the gate stepper — pure display of `gate_state`;
 *   (b) the non-dismissible deadline banner, shown while the matter is in
 *       corpus_processing or facts_review and has any deadline candidates;
 *   (c) the documents panel (list + upload + run-ingest SSE + dedup queue);
 *   (d) the active gate screen, dispatched off the gates envelope: FactsReviewCard at
 *       facts_review, StrategyIntakeCard at strategy_intake, the EvidenceWorkbench (G2a) at
 *       analysis_running (run-button mode) and evidence_review, and a neutral state card otherwise.
 *
 * Gate honesty: the stepper reflects `matter.gate_state` from the fetched view; the gate
 * card reflects the `GateEnvelope`. When the ingest stream emits `gate_ready`, we invalidate
 * BOTH the matter and gate queries so the next render draws the new state from refetched
 * backend data — we never advance the stepper or the card client-side.
 *
 * `params` is a Promise in Next 15; unwrapped with React.use().
 */

import { use } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet } from "@/lib/api";
import { gateKey, useGate } from "@/lib/gates";
import type {
  ComplianceReviewVM,
  EvidenceReviewVM,
  FactsVM,
  GateEnvelope,
  MatterView,
  PackageVM,
  PlanReviewVM,
  StrategyIntakeVM,
} from "@/lib/types";
import { CompliancePanel } from "@/components/compliance-panel";
import { DeadlineBanner } from "@/components/deadline-banner";
import { DemandGenerationCard } from "@/components/demand-generation-card";
import { DocumentsPanel } from "@/components/documents-panel";
import { EvidenceWorkbench } from "@/components/evidence-workbench";
import { FactsReviewCard } from "@/components/facts-review-card";
import { GateStepper } from "@/components/gate-stepper";
import { PackageCard } from "@/components/package-card";
import { PlanReviewCard } from "@/components/plan-review-card";
import { StrategyIntakeCard } from "@/components/strategy-intake-card";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const matterKey = (id: string) => ["matter", id] as const;

/** Gate states during which the deadline banner is shown (pre-G1-confirmation window). */
const BANNER_STATES = new Set(["corpus_processing", "facts_review"]);

export default function MatterDashboardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const queryClient = useQueryClient();

  const matterQuery = useQuery({
    queryKey: matterKey(id),
    queryFn: () => apiGet<MatterView>(`/api/matters/${id}`),
  });

  const gateQuery = useGate(id);

  function handleGateReady() {
    // A real gate_ready frame arrived — refetch the matter (stepper) AND the gate envelope
    // (active gate card) so both redraw from backend state. Never advanced client-side.
    void queryClient.invalidateQueries({ queryKey: matterKey(id) });
    void queryClient.invalidateQueries({ queryKey: gateKey(id) });
  }

  if (matterQuery.isLoading) {
    return <p className="text-sm text-ink-muted">Loading matter…</p>;
  }

  if (matterQuery.isError) {
    const error = matterQuery.error;
    const code =
      error instanceof ApiError
        ? (error.body.error ?? error.body.detail ?? "Could not load matter.")
        : "Could not load matter.";
    return (
      <Card>
        <CardHeader>
          <CardTitle>Matter unavailable</CardTitle>
          <CardDescription data-testid="matter-error">{code}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const matter = matterQuery.data;
  if (matter === undefined) {
    // Not loading, not errored, but no data — treat as loading (keeps `matter` narrowed).
    return <p className="text-sm text-ink-muted">Loading matter…</p>;
  }

  const showBanner =
    BANNER_STATES.has(matter.gate_state) && matter.deadline_candidates.length > 0;

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-ink">
            {matter.client_display_name}
          </h1>
          <p className="text-sm text-ink-muted">
            {matter.claim_type.toUpperCase()} · {matter.jurisdiction} · incident{" "}
            {matter.incident_date}
          </p>
        </div>
        <Badge variant="secondary">registry v{matter.registry_version}</Badge>
      </div>

      {/* (a) Gate stepper */}
      <Card>
        <CardHeader>
          <CardTitle>Progress</CardTitle>
        </CardHeader>
        <CardContent>
          <GateStepper current={matter.gate_state} />
        </CardContent>
      </Card>

      {/* (b) Deadline banner — non-dismissible */}
      {showBanner && <DeadlineBanner candidates={matter.deadline_candidates} />}

      {/* (c) Documents panel */}
      <DocumentsPanel matterId={matter.id} onGateReady={handleGateReady} />

      {/* (d) Active gate screen — dispatched off the gates envelope. */}
      <GatePanel matterId={matter.id} query={gateQuery} onGateReady={handleGateReady} />
    </div>
  );
}

// ---------------------------------------------------------------------------------------

/** Dispatch the active gate screen from the envelope; own its loading / error / neutral states. */
function GatePanel({
  matterId,
  query,
  onGateReady,
}: {
  matterId: string;
  query: ReturnType<typeof useGate>;
  onGateReady: (gate: string) => void;
}) {
  if (query.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Gate</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-ink-muted">Loading gate…</p>
        </CardContent>
      </Card>
    );
  }

  if (query.isError || query.data === undefined) {
    const code =
      query.error instanceof ApiError
        ? (query.error.body.error ?? query.error.body.detail ?? "Could not load the gate.")
        : "Could not load the gate.";
    return (
      <Card>
        <CardHeader>
          <CardTitle>Gate unavailable</CardTitle>
          <CardDescription data-testid="gate-error">{code}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const envelope: GateEnvelope = query.data;

  if (envelope.gate === "facts_review") {
    return (
      <FactsReviewCard
        matterId={matterId}
        vm={envelope.view_model as FactsVM}
        payloadVersion={envelope.payload_version}
        roleAffordances={envelope.role_affordances}
      />
    );
  }

  if (envelope.gate === "strategy_intake") {
    return (
      <StrategyIntakeCard
        matterId={matterId}
        vm={envelope.view_model as StrategyIntakeVM}
        payloadVersion={envelope.payload_version}
        roleAffordances={envelope.role_affordances}
      />
    );
  }

  if (envelope.gate === "analysis_running") {
    // The matter parks here until someone runs the analysis. The workbench renders the run button
    // (analysisRunning mode); only a real gate_ready advances the view. The evidence VM does not
    // exist yet at this state, so a minimal shell is passed — the workbench ignores it while running.
    return (
      <EvidenceWorkbench
        matterId={matterId}
        vm={EMPTY_EVIDENCE_VM}
        payloadVersion={envelope.payload_version}
        roleAffordances={envelope.role_affordances}
        analysisRunning
        onGateReady={onGateReady}
      />
    );
  }

  if (envelope.gate === "evidence_review") {
    return (
      <EvidenceWorkbench
        matterId={matterId}
        vm={envelope.view_model as EvidenceReviewVM}
        payloadVersion={envelope.payload_version}
        roleAffordances={envelope.role_affordances}
        analysisRunning={false}
        onGateReady={onGateReady}
      />
    );
  }

  if (envelope.gate === "plan_review") {
    return (
      <PlanReviewCard
        matterId={matterId}
        vm={envelope.view_model as PlanReviewVM}
        payloadVersion={envelope.payload_version}
        roleAffordances={envelope.role_affordances}
      />
    );
  }

  if (envelope.gate === "drafting") {
    // The generate stream IS the surface here (the drafting VM is a minimal state placeholder).
    return <DemandGenerationCard matterId={matterId} onGateReady={onGateReady} />;
  }

  if (envelope.gate === "compliance_review") {
    return (
      <CompliancePanel
        matterId={matterId}
        vm={envelope.view_model as ComplianceReviewVM}
        payloadVersion={envelope.payload_version}
        roleAffordances={envelope.role_affordances}
      />
    );
  }

  if (envelope.gate === "package_assembly" || envelope.gate === "package_ready") {
    return (
      <PackageCard
        matterId={matterId}
        gate={envelope.gate}
        vm={envelope.view_model as PackageVM}
        onGateReady={onGateReady}
      />
    );
  }

  // Every other state: the honest minimal card from the envelope's placeholder VM.
  return <NeutralGateCard gate={envelope.gate} />;
}

/** An empty evidence VM shell for the analysis_running state (the workbench shows only the run banner). */
const EMPTY_EVIDENCE_VM: EvidenceReviewVM = {
  chronology: { rows: [], conflicts: 0, parked: 0 },
  ledger: null,
  risk_flags: [],
  exhibits: { entries: [], blocking: [] },
  dedup_pending: 0,
};

/** A neutral state card for gates whose dedicated UI hasn't landed — no fake affordances. */
function NeutralGateCard({ gate }: { gate: GateEnvelope["gate"] }) {
  return (
    <Card data-testid="neutral-gate-card" data-gate={gate}>
      <CardHeader>
        <CardTitle>Current gate</CardTitle>
        <CardDescription>
          This matter is at <span className="font-medium text-ink">{gate}</span>. Its dedicated
          screen lands in a later milestone.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}
