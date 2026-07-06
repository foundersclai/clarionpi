import { describe, expect, it } from "vitest";
import { MONEY_PARSE_ERROR, centsToDollars, dollarsToCents } from "@/lib/money";

describe("dollarsToCents", () => {
  it("parses a grouped decimal amount to integer cents", () => {
    expect(dollarsToCents("1,234.56")).toBe(123456);
    expect(dollarsToCents("85,000.00")).toBe(8500000);
  });

  it("parses whole-dollar and single-decimal amounts", () => {
    expect(dollarsToCents("85000")).toBe(8500000);
    expect(dollarsToCents("100")).toBe(10000);
    expect(dollarsToCents("12.5")).toBe(1250); // one decimal -> tenths, padded to cents
  });

  it("accepts an optional leading $ and surrounding whitespace", () => {
    expect(dollarsToCents("  $42.00 ")).toBe(4200);
    expect(dollarsToCents("$1,000")).toBe(100000);
  });

  it("treats an empty / whitespace-only field as a cleared value (null)", () => {
    expect(dollarsToCents("")).toBeNull();
    expect(dollarsToCents("   ")).toBeNull();
  });

  it("rejects non-numeric input with the parse-error sentinel", () => {
    expect(dollarsToCents("abc")).toBe(MONEY_PARSE_ERROR);
    expect(dollarsToCents("12.3.4")).toBe(MONEY_PARSE_ERROR);
    expect(dollarsToCents("1.2e3")).toBe(MONEY_PARSE_ERROR); // no exponent form
    expect(dollarsToCents("$")).toBe(MONEY_PARSE_ERROR); // bare symbol, nothing to parse
  });

  it("strips thousands separators leniently (grouping is not validated)", () => {
    // We normalize commas out then validate the digits; oddly-grouped input still parses.
    expect(dollarsToCents("1,00,0")).toBe(100000);
  });

  it("rejects more than two decimal places (no sub-cent precision)", () => {
    expect(dollarsToCents("1.234")).toBe(MONEY_PARSE_ERROR);
    expect(dollarsToCents("0.001")).toBe(MONEY_PARSE_ERROR);
  });

  it("rejects negative amounts (anchor / property-damage are positive)", () => {
    expect(dollarsToCents("-5")).toBe(MONEY_PARSE_ERROR);
    expect(dollarsToCents("-1,000.00")).toBe(MONEY_PARSE_ERROR);
  });

  it("parses zero", () => {
    expect(dollarsToCents("0")).toBe(0);
    expect(dollarsToCents("0.00")).toBe(0);
  });
});

describe("centsToDollars", () => {
  it("formats integer cents as grouped two-decimal dollars", () => {
    expect(centsToDollars(8500000)).toBe("85,000.00");
    expect(centsToDollars(123456)).toBe("1,234.56");
    expect(centsToDollars(0)).toBe("0.00");
  });

  it("formats null as an empty string (unset, not zero)", () => {
    expect(centsToDollars(null)).toBe("");
  });

  it("round-trips through dollarsToCents", () => {
    for (const cents of [0, 4200, 123456, 8500000]) {
      expect(dollarsToCents(centsToDollars(cents))).toBe(cents);
    }
  });
});
