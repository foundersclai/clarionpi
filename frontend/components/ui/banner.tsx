import { type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils";

type BannerTone = "warning" | "error" | "info";

export interface BannerProps extends HTMLAttributes<HTMLDivElement> {
  tone?: BannerTone;
  /**
   * Optional short heading rendered bold above the children. Named `heading` (not `title`)
   * to avoid clashing with the DOM `title` attribute on HTMLAttributes.
   */
  heading?: ReactNode;
}

const TONE_CLASSES: Record<BannerTone, string> = {
  warning: "border-warning/50 bg-warning/10 text-warning-foreground",
  error: "border-danger/50 bg-danger/10 text-danger",
  info: "border-info/50 bg-info/10 text-info",
};

/**
 * Full-width alert bar. **By design it has NO dismiss / close affordance** — the deadline
 * banner it backs must stay visible until the attorney confirms the deadlines at G1
 * (invariant 4). Do not add a close button; that would let a legally-significant warning be
 * hidden. `role="alert"` so assistive tech announces it.
 */
export function Banner({
  className,
  tone = "info",
  heading,
  children,
  ...props
}: BannerProps) {
  return (
    <div
      role="alert"
      className={cn(
        "w-full rounded-md border px-4 py-3 text-sm",
        TONE_CLASSES[tone],
        className,
      )}
      {...props}
    >
      {heading !== undefined && (
        <p className="mb-1 font-semibold leading-tight">{heading}</p>
      )}
      {children}
    </div>
  );
}
