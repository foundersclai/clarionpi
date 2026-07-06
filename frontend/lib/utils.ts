import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge conditional class names, letting later Tailwind utilities win over earlier ones
 * (e.g. `cn("px-2", condition && "px-4")` → `px-4`). The one class-composition helper the
 * UI primitives share.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
