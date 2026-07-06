"use client";

/**
 * DeadlineBanner — the non-dismissible deadline banner (invariant 4).
 *
 * Renders the matter's rules-computed SOL / notice-of-claim candidates. It has NO dismiss
 * affordance by design: these deadlines stay in view until the attorney confirms them at
 * G1. The banner is a pure projection of `candidates` — it never marks a candidate
 * confirmed itself (that is a backend transition), it only reflects `confirmed` /
 * `verify_status` as they arrive.
 *
 * `verify_status: "unverified"` renders an amber "pending counsel audit" badge — the
 * rules-derived date has NOT been audited by counsel yet, and the copy says exactly that
 * (no over-claiming certainty on a machine-computed deadline).
 */

import type { DeadlineCandidate, DeadlineKind } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";

export interface DeadlineBannerProps {
  candidates: DeadlineCandidate[];
}

const KIND_LABELS: Record<DeadlineKind, string> = {
  sol: "Statute of limitations",
  notice_of_claim: "Notice of claim",
};

function kindLabel(kind: DeadlineKind): string {
  return KIND_LABELS[kind] ?? kind;
}

/** Verify-status badge — amber when unverified, green when a counsel audit has verified it. */
function VerifyBadge({ status }: { status: DeadlineCandidate["verify_status"] }) {
  if (status === "verified") {
    return <Badge variant="success">Counsel-verified</Badge>;
  }
  return <Badge variant="warning">Pending counsel audit</Badge>;
}

export function DeadlineBanner({ candidates }: DeadlineBannerProps) {
  if (candidates.length === 0) {
    return null;
  }

  return (
    <Banner
      tone="warning"
      heading="Deadlines pending attorney confirmation"
      data-testid="deadline-banner"
    >
      <p className="mb-2 text-xs">
        These deadlines are computed from the jurisdiction&apos;s rules and must be confirmed
        at facts review (G1). They cannot be dismissed until then.
      </p>
      <ul className="flex flex-col gap-2" data-testid="deadline-list">
        {candidates.map((candidate, index) => (
          <li
            key={`${candidate.kind}-${candidate.date}-${index}`}
            data-testid="deadline-item"
            data-kind={candidate.kind}
            className="flex flex-wrap items-center gap-x-3 gap-y-1"
          >
            <span className="font-medium">{kindLabel(candidate.kind)}</span>
            <span className="font-mono text-sm">{candidate.date}</span>
            <span className="text-xs text-ink-muted">{candidate.statute_cite}</span>
            <VerifyBadge status={candidate.verify_status} />
            {candidate.confirmed ? (
              <Badge variant="success">Confirmed</Badge>
            ) : (
              <Badge variant="outline">Unconfirmed</Badge>
            )}
          </li>
        ))}
      </ul>
    </Banner>
  );
}
