import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  blobUrlFor,
  getProvenance,
  provenanceKey,
  toProvenanceAnchor,
  type ProvenanceResponse,
} from "@/lib/provenance";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const OK: ProvenanceResponse = {
  token_id: "FACT_3",
  display_form: "cervical strain",
  outcome: "ok",
  source: "extractor",
  anchors: [
    { document_id: "doc-1", page: 3, bbox: null, blob_url: "/api/documents/doc-1/blob", page_count: 12, superseded: false },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("blobUrlFor — the one sanctioned URL constructor", () => {
  it("builds the pinned same-origin blob route", () => {
    expect(blobUrlFor("doc-42")).toBe("/api/documents/doc-42/blob");
  });
});

describe("toProvenanceAnchor — normalize a lean anchor", () => {
  it("builds blob_url via blobUrlFor when the wire anchor omits it (anchors mode)", () => {
    const a = toProvenanceAnchor({ document_id: "doc-9", page: 2 });
    expect(a.blob_url).toBe("/api/documents/doc-9/blob");
    expect(a.bbox).toBeNull();
    expect(a.page_count).toBe(0);
    expect(a.superseded).toBe(false);
  });

  it("keeps the server-sent blob_url verbatim when present (token mode)", () => {
    const a = toProvenanceAnchor({
      document_id: "doc-1",
      page: 5,
      bbox: null,
      blob_url: "/api/documents/doc-1/blob?rev=2",
      page_count: 20,
      superseded: true,
    });
    expect(a.blob_url).toBe("/api/documents/doc-1/blob?rev=2");
    expect(a.page_count).toBe(20);
    expect(a.superseded).toBe(true);
  });
});

describe("provenanceKey", () => {
  it("keys by matter + token", () => {
    expect(provenanceKey("m1", "FACT_3")).toEqual(["provenance", "m1", "FACT_3"]);
  });
});

describe("getProvenance — URL shape + typed refusals", () => {
  it("GETs the pinned bare-token route (token id url-encoded)", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, OK));
    const res = await getProvenance("m1", "FACT_3");
    expect(res).toEqual(OK);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/matters/m1/provenance/FACT_3");
  });

  it("throws a typed ApiError on 404 token_not_found", async () => {
    // A fresh Response per call — a Response body can be consumed only once.
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(jsonResponse(404, { error: "token_not_found" })),
    );
    const err = await getProvenance("m1", "NOPE_9").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, body: { error: "token_not_found" } });
  });

  it("throws a typed ApiError on 422 invalid_token_id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, { error: "invalid_token_id" }),
    );
    await expect(getProvenance("m1", "bad id")).rejects.toMatchObject({
      status: 422,
      body: { error: "invalid_token_id" },
    });
  });
});
