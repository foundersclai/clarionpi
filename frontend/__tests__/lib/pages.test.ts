import { describe, expect, it } from "vitest";
import { PageRangeError, formatPageRanges, parsePageRanges } from "@/lib/pages";

describe("parsePageRanges", () => {
  it("expands a mixed list of singles and ranges, canonical + sorted", () => {
    expect(parsePageRanges("1-4,7", 10)).toEqual([1, 2, 3, 4, 7]);
    expect(parsePageRanges("7,1-2", 10)).toEqual([1, 2, 7]);
  });

  it("dedupes overlapping ranges and repeated singles", () => {
    expect(parsePageRanges("1-3,2-4,4", 10)).toEqual([1, 2, 3, 4]);
    expect(parsePageRanges("5,5,5", 10)).toEqual([5]);
  });

  it("tolerates whitespace around tokens and separators", () => {
    expect(parsePageRanges("  1 - 3 , 5 ", 10)).toEqual([1, 2, 3, 5]);
  });

  it("treats an empty / whitespace-only string as an empty selection", () => {
    expect(parsePageRanges("", 10)).toEqual([]);
    expect(parsePageRanges("   ", 10)).toEqual([]);
  });

  it("rejects a page above the max with a typed error", () => {
    const result = parsePageRanges("1-11", 10);
    expect(result).toBeInstanceOf(PageRangeError);
    expect((result as PageRangeError).message).toMatch(/outside 1–10/);
  });

  it("rejects page zero and a range starting at zero", () => {
    expect(parsePageRanges("0", 10)).toBeInstanceOf(PageRangeError);
    expect(parsePageRanges("0-3", 10)).toBeInstanceOf(PageRangeError);
  });

  it("rejects a reversed range", () => {
    const result = parsePageRanges("7-4", 10);
    expect(result).toBeInstanceOf(PageRangeError);
    expect((result as PageRangeError).message).toMatch(/reversed/);
  });

  it("rejects garbage, negatives, decimals, and malformed ranges", () => {
    for (const bad of ["abc", "-3", "1.5", "1-2-3", "1,,3", "1,", "3-"]) {
      expect(parsePageRanges(bad, 10), bad).toBeInstanceOf(PageRangeError);
    }
  });
});

describe("formatPageRanges", () => {
  it("collapses consecutive runs into ranges", () => {
    expect(formatPageRanges([1, 2, 3, 4, 7])).toBe("1-4,7");
    expect(formatPageRanges([1, 3, 5])).toBe("1,3,5");
    expect(formatPageRanges([2])).toBe("2");
  });

  it("dedupes + sorts defensively before formatting", () => {
    expect(formatPageRanges([4, 2, 3, 2, 1])).toBe("1-4");
  });

  it("formats an empty list as an empty string", () => {
    expect(formatPageRanges([])).toBe("");
  });
});

describe("round-trip", () => {
  it("parse ∘ format is stable for canonical inputs", () => {
    for (const s of ["1-4,7", "1,3,5", "2", "1-10"]) {
      const parsed = parsePageRanges(s, 20);
      expect(parsed).not.toBeInstanceOf(PageRangeError);
      expect(formatPageRanges(parsed as number[])).toBe(s);
    }
  });

  it("format ∘ parse recovers the same page set", () => {
    for (const pages of [[1, 2, 3, 4, 7], [5], [], [1, 3, 5, 6, 7]]) {
      const formatted = formatPageRanges(pages);
      const reparsed = parsePageRanges(formatted, 20);
      expect(reparsed).toEqual(Array.from(new Set(pages)).sort((a, b) => a - b));
    }
  });
});
