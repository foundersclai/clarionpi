import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type ButtonVariant = "default" | "outline" | "ghost" | "destructive";
type ButtonSize = "default" | "sm" | "lg" | "icon";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  default: "bg-accent text-accent-foreground hover:bg-accent/90",
  outline: "border border-border bg-surface text-ink hover:bg-surface-muted",
  ghost: "bg-transparent text-ink hover:bg-surface-muted",
  destructive: "bg-danger text-danger-foreground hover:bg-danger/90",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  default: "h-9 px-4 py-2 text-sm",
  sm: "h-8 px-3 text-xs",
  lg: "h-10 px-6 text-sm",
  icon: "h-9 w-9",
};

/**
 * Button. Note: a `disabled` button is genuinely non-interactive — do NOT use it to gate a
 * legally-blocked action. Blocked-but-clickable actions stay enabled and surface an inline
 * reason (design rule: no gray-outs for legal blocks). `disabled` is for in-flight/loading.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", type, ...props }, ref) => (
    <button
      ref={ref}
      // Default to type="button" so a Button inside a form doesn't submit by accident.
      type={type ?? "button"}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium",
        "transition-colors focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-accent focus-visible:ring-offset-1",
        "disabled:pointer-events-none disabled:opacity-50",
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = "Button";
