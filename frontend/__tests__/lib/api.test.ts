import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiGet, apiPost } from "@/lib/api";

/** Build a Response-like stub for the mocked fetch. */
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiGet", () => {
  it("returns the parsed body on a 200", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { id: "m1", gate_state: "corpus_processing" }));

    const result = await apiGet<{ id: string; gate_state: string }>("/api/matters/m1");

    expect(result).toEqual({ id: "m1", gate_state: "corpus_processing" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/matters/m1",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });

  it("sends credentials: include", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, {}));

    await apiGet("/api/anything");

    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.credentials).toBe("include");
  });

  it("throws a typed ApiError surfacing body.error on a non-2xx", async () => {
    // A Response body can only be read once, so return a FRESH Response per call.
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse(422, {
        error: "jurisdiction_unsupported",
        detail: "AZ only",
        supported: ["AZ"],
      }),
    );

    await expect(apiGet("/api/matters")).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
    });

    // And the body is preserved for inline rendering.
    try {
      await apiGet("/api/matters");
      throw new Error("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const apiError = error as ApiError;
      expect(apiError.body.error).toBe("jurisdiction_unsupported");
      expect(apiError.body.supported).toEqual(["AZ"]);
    }
  });
});

describe("apiPost", () => {
  it("serializes the body as JSON and sets Content-Type", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(201, { id: "m2" }));

    await apiPost("/api/matters", { client_display_name: "Doe" });

    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("include");
    expect(init?.body).toBe(JSON.stringify({ client_display_name: "Doe" }));
    expect(
      (init?.headers as Record<string, string>)["Content-Type"],
    ).toBe("application/json");
  });

  it("tolerates an empty 2xx body (returns null)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 201 }));

    const result = await apiPost("/api/uploads/s1/commit");
    expect(result).toBeNull();
  });
});
