import { type LabelHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** Form label. Pair with an input via `htmlFor`. */
export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        "text-sm font-medium leading-none text-ink",
        "peer-disabled:cursor-not-allowed peer-disabled:opacity-70",
        className,
      )}
      {...props}
    />
  );
}
