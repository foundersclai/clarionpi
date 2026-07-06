/**
 * Page-range boundary — the ONLY place a human-typed page selection ("1-4,7,9-11") converts
 * to/from the 1-based integer-page list the exhibit-pick wire carries. Mirrors the money
 * boundary's discipline: never send a guess. A malformed, out-of-range, or reversed range
 * returns a typed {@link PageRangeError} the caller renders inline rather than submitting a
 * silently-coerced list.
 *
 * Strictness (defects if violated):
 *   - Pages are 1-based; `0`, negatives, and `> max` are rejected.
 *   - `parsePageRanges` DEDUPES overlaps and sorts ascending — the returned list is canonical
 *     (so "3,1-2,2" → [1,2,3]), and never contains a value outside `1..max`.
 *   - A reversed range ("7-4"), a non-integer, or stray garbage → error (never a partial parse).
 *   - The empty string parses to `[]` (an empty selection is legal — a document with no pages
 *     included yet). Whitespace around tokens and separators is tolerated.
 */

/** The typed error a strict page-range parse returns; `message` is the inline copy the UI shows. */
export class PageRangeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PageRangeError";
  }
}

/**
 * Parse a page-range string into a canonical ascending, deduped list of 1-based page numbers.
 *
 *   - `""` / whitespace-only → `[]` (an empty, legal selection).
 *   - "1-4,7" → `[1, 2, 3, 4, 7]`; "3,1-2,2" → `[1, 2, 3]` (deduped + sorted).
 *   - Any token that is not `N` or `N-M` with `1 <= N <= M <= max` → {@link PageRangeError}.
 *
 * @param input the raw string from the range input.
 * @param max   the document's `page_count` — the inclusive upper bound (a page above it is a typo).
 */
export function parsePageRanges(input: string, max: number): number[] | PageRangeError {
  const trimmed = input.trim();
  if (trimmed.length === 0) {
    return [];
  }
  const pages = new Set<number>();
  for (const rawToken of trimmed.split(",")) {
    const token = rawToken.trim();
    if (token.length === 0) {
      // A trailing / doubled comma ("1,,3" or "1,") — refuse rather than silently skip.
      return new PageRangeError(`Empty range segment in "${input}".`);
    }
    const dashParts = token.split("-");
    if (dashParts.length === 1) {
      const n = parseOnePage(dashParts[0]);
      if (n === null) return new PageRangeError(`"${token}" is not a page number.`);
      if (n < 1 || n > max) return new PageRangeError(`Page ${n} is outside 1–${max}.`);
      pages.add(n);
    } else if (dashParts.length === 2) {
      const start = parseOnePage(dashParts[0]);
      const end = parseOnePage(dashParts[1]);
      if (start === null || end === null) {
        return new PageRangeError(`"${token}" is not a valid page range.`);
      }
      if (start > end) return new PageRangeError(`Range "${token}" is reversed.`);
      if (start < 1 || end > max) {
        return new PageRangeError(`Range "${token}" is outside 1–${max}.`);
      }
      for (let p = start; p <= end; p++) pages.add(p);
    } else {
      // "1-2-3" and the like.
      return new PageRangeError(`"${token}" is not a valid page range.`);
    }
  }
  return Array.from(pages).sort((a, b) => a - b);
}

/**
 * Format a list of 1-based page numbers back into a canonical range string, collapsing runs
 * of consecutive pages ("1,2,3,4,7" → "1-4,7"). The input is deduped + sorted defensively so
 * the output is stable regardless of the caller's ordering. `[]` → "".
 */
export function formatPageRanges(pages: number[]): string {
  const sorted = Array.from(new Set(pages)).sort((a, b) => a - b);
  if (sorted.length === 0) {
    return "";
  }
  const segments: string[] = [];
  let runStart = sorted[0];
  let prev = sorted[0];
  for (let i = 1; i <= sorted.length; i++) {
    const current = sorted[i];
    // Close the current run when the sequence breaks or we run off the end.
    if (i === sorted.length || current !== prev + 1) {
      segments.push(runStart === prev ? `${runStart}` : `${runStart}-${prev}`);
      if (i < sorted.length) {
        runStart = current;
        prev = current;
      }
    } else {
      prev = current;
    }
  }
  return segments.join(",");
}

/** Parse one page token to a positive integer, or `null` if it isn't a bare non-negative integer. */
function parseOnePage(raw: string): number | null {
  const token = raw.trim();
  // Strict: only digits (no sign, no decimal, no whitespace-in-the-middle survives the digit test).
  if (!/^\d+$/.test(token)) {
    return null;
  }
  const n = Number(token);
  return Number.isSafeInteger(n) ? n : null;
}
