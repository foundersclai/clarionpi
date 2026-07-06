import { type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type BadgeVariant =
  | "default"
  | "outline"
  | "secondary"
  | "warning"
  | "success"
  | "danger"
  | "info";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default: "bg-accent text-accent-foreground",
  outline: "border border-border text-ink",
  secondary: "bg-surface-muted text-ink-muted",
  warning: "bg-warning/15 text-warning-foreground border border-warning/40",
  success: "bg-success/15 text-success border border-success/40",
  danger: "bg-danger/15 text-danger border border-danger/40",
  info: "bg-info/15 text-info border border-info/40",
};

/** Small status pill. `warning`/`success`/`danger`/`info` map to the semantic tokens. */
export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    />
  );
}
