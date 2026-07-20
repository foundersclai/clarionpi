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

/**
 * Render a billing line's service date, or its service PERIOD when the bill declared a date range
 * (`end` present and distinct from `start`): "2025-03-24 – 2025-06-16". A single-date line (end
 * null/undefined, or equal to start) shows just the date. The FE never collapses a stated span to
 * a single day, nor invents one — it displays exactly what the server recorded.
 */
export function formatServiceDate(start: string, end?: string | null): string {
  if (!end || end === start) return start;
  return `${start} – ${end}`;
}
