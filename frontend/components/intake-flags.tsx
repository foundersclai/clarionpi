/**
 * IntakeFlags — the matter header's read-only pilot-intake row (WI-2).
 *
 * A pure projection of the four stored eligibility answers — part of the file's audit
 * story, never editable here (eligibility is a creation-time check; the stored answers
 * are what the attorney attested at intake). "no" renders neutral; "unknown" (a matter
 * that predates the preflight) and "yes" (impossible via the create API, reachable only
 * by out-of-band writes) render amber so an unattested file is visible at a glance.
 */

import type { IntakeFlagAnswer, IntakeFlagKey, MatterView } from "@/lib/types";
import { INTAKE_FLAG_KEYS } from "@/lib/types";
import { Badge } from "@/components/ui/badge";

/** Short display labels, per flag (the create form asks the long-form questions). */
const FLAG_LABELS: Record<IntakeFlagKey, string> = {
  public_entity_involved: "Public entity",
  plaintiff_is_minor: "Minor plaintiff",
  wrongful_death: "Wrongful death",
  coverage_dispute: "Coverage dispute",
};

const ANSWER_LABELS: Record<IntakeFlagAnswer, string> = {
  yes: "yes",
  no: "no",
  unknown: "unknown",
};

export interface IntakeFlagsProps {
  matter: Pick<MatterView, IntakeFlagKey>;
}

export function IntakeFlags({ matter }: IntakeFlagsProps) {
  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      data-testid="intake-flags"
      aria-label="Pilot intake answers"
    >
      <span className="text-xs text-ink-muted">Intake:</span>
      {INTAKE_FLAG_KEYS.map((key) => {
        const answer = matter[key];
        return (
          <Badge
            key={key}
            variant={answer === "no" ? "outline" : "warning"}
            data-testid="intake-flag"
            data-flag={key}
            data-answer={answer}
          >
            {FLAG_LABELS[key]}: {ANSWER_LABELS[answer]}
          </Badge>
        );
      })}
    </div>
  );
}
