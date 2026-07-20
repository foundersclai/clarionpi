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

describe("error-body normalization", () => {
  it("flattens the auth layer's nested {detail:{error}} 401 into a flat {error, detail}", async () => {
    // Regression: the auth dependency serializes as { detail: { error: "unauthenticated" } }.
    // Left nested, a renderer doing `body.error ?? body.detail` gets the raw {error} OBJECT and
    // crashes React ("Objects are not valid as a React child"). It must arrive flattened.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(401, { detail: { error: "unauthenticated" } }),
    );
    try {
      await apiGet("/api/matters/m1");
      throw new Error("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const body = (error as ApiError).body;
      expect(body.error).toBe("unauthenticated");
      expect(typeof body.detail).toBe("string"); // NEVER an object
      expect(body.detail).toBe("unauthenticated");
    }
  });

  it("lifts sibling fields out of a nested detail and keeps detail a string", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(403, { detail: { error: "role_forbidden", actual: ["paralegal"] } }),
    );
    try {
      await apiGet("/api/x");
      throw new Error("should have thrown");
    } catch (error) {
      const body = (error as ApiError).body;
      expect(body.error).toBe("role_forbidden");
      expect(body.actual).toEqual(["paralegal"]);
      expect(typeof body.detail).toBe("string");
    }
  });

  it("passes a flat {error, detail} body through unchanged", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(404, { error: "matter_not_found", detail: "no matter m1" }),
    );
    try {
      await apiGet("/api/matters/m1");
      throw new Error("should have thrown");
    } catch (error) {
      const body = (error as ApiError).body;
      expect(body.error).toBe("matter_not_found");
      expect(body.detail).toBe("no matter m1");
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
