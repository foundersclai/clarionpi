/**
 * Money boundary — the ONLY place dollars (attorney-facing strings) convert to/from the
 * integer cents the wire carries. Mirrors the backend's money discipline (a `Cents` int,
 * never a float): the UI presents dollars, the wire sends cents, and the conversion happens
 * exactly here so no other module has to reason about the factor-of-100.
 *
 * Design rule: never send a guess. `dollarsToCents` STRICT-parses — an unparseable or
 * negative amount returns a sentinel the caller renders as an inline error rather than a
 * silently-coerced number. An empty field means "clear this money value" → null (the wire
 * accepts a nullable cents field; null blanks the stored value).
 */

/** Marker the strict parse returns when the input is present but not a valid non-negative amount. */
export const MONEY_PARSE_ERROR = Symbol("money_parse_error");

/**
 * Parse a dollars string into integer cents.
 *
 *   - Empty / whitespace-only → `null` (clears the field; a blank money input is legal).
 *   - A valid non-negative amount → integer cents ("1,234.56" → 123456, "85,000" → 8500000).
 *   - Anything else (letters, negatives, >2 decimals, stray symbols) → {@link MONEY_PARSE_ERROR}.
 *
 * Accepts thousands separators and an optional leading `$`; at most two decimal places (cents
 * have no sub-cent precision). Negatives are rejected — anchor / property-damage amounts are
 * positive, and a negative here is far likelier a typo than an intent, so we refuse loudly.
 */
export function dollarsToCents(input: string): number | null | typeof MONEY_PARSE_ERROR {
  const trimmed = input.trim();
  if (trimmed.length === 0) {
    return null;
  }
  // Strip a single leading currency symbol and thousands separators, then validate the shape.
  const cleaned = trimmed.replace(/^\$/, "").replace(/,/g, "");
  // Non-negative decimal with at most two fractional digits. No leading `+`/`-`, no exponent.
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) {
    return MONEY_PARSE_ERROR;
  }
  const [wholePart, fracPartRaw = ""] = cleaned.split(".");
  const fracPart = fracPartRaw.padEnd(2, "0"); // "5" → "50", "" → "00"
  const cents = Number(wholePart) * 100 + Number(fracPart);
  if (!Number.isFinite(cents) || !Number.isSafeInteger(cents)) {
    return MONEY_PARSE_ERROR;
  }
  return cents;
}

/**
 * Format integer cents as a dollars string for a money input's initial value.
 *
 *   - `null` → "" (an empty input, not "0.00" — a null money value is "unset", not zero).
 *   - integer cents → fixed two-decimal dollars with thousands separators ("85,000.00").
 *
 * The grouping is for display of the initial/current value; the input accepts either grouped
 * or ungrouped on the way back (see {@link dollarsToCents}).
 */
export function centsToDollars(cents: number | null): string {
  if (cents === null) {
    return "";
  }
  const dollars = cents / 100;
  return dollars.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}
