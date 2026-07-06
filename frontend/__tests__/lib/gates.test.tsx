import { afterEach, describe, expect, it, vi } from "vitest";
import { type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import {
  GateStaleError,
  gateKey,
  mintIdempotencyKey,
  submitGate,
  useSubmitGate,
} from "@/lib/gates";
import type { GateEnvelope } from "@/lib/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("mintIdempotencyKey", () => {
  it("mints a key within the backend's 8..64 char bound", () => {
    for (let i = 0; i < 50; i++) {
      const key = mintIdempotencyKey();
      expect(key.length).toBeGreaterThanOrEqual(8);
      expect(key.length).toBeLessThanOrEqual(64);
    }
  });

  it("mints a fresh key each call (no accidental replay)", () => {
    const a = mintIdempotencyKey();
    const b = mintIdempotencyKey();
    expect(a).not.toBe(b);
  });
});

describe("submitGate body shape (frozen)", () => {
  it("sends EXACTLY the closed GateSubmit keys, mint the idempotency_key, no extra keys", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r1" }));

    await submitGate("m1", "facts_review", {
      action: "edit",
      payload_version: 3,
      edits: { deadline_confirmations: [{ rule_id: "A.R.S. § 12-542", confirmed: true }] },
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/matters/m1/gates/facts_review/submit");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(init?.body as string);

    // The key set is frozen — snapshot it so a stray overlay/view-model field trips the test.
    expect(Object.keys(body).sort()).toEqual(
      ["action", "edits", "idempotency_key", "payload_version"].sort(),
    );
    expect(body.action).toBe("edit");
    expect(body.payload_version).toBe(3);
    expect(body.edits).toEqual({
      deadline_confirmations: [{ rule_id: "A.R.S. § 12-542", confirmed: true }],
    });
    expect(typeof body.idempotency_key).toBe("string");
    expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8);
    expect(body.idempotency_key.length).toBeLessThanOrEqual(64);
  });

  it("omits `edits` and `override_reason` when not supplied (approve with no edits)", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { result: {}, matter: {}, record_id: "r2" }));

    await submitGate("m1", "strategy_intake", { action: "approve", payload_version: 5 });

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(Object.keys(body).sort()).toEqual(
      ["action", "idempotency_key", "payload_version"].sort(),
    );
    expect(body).not.toHaveProperty("edits");
    expect(body).not.toHaveProperty("override_reason");
  });
});

describe("useSubmitGate stale-fence", () => {
  it("auto-refetches the envelope and rethrows GateStaleError on stale_payload_version", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    // Seed the gate query so refetchQueries has something to re-run (and we can observe it).
    const envelope: GateEnvelope = {
      gate: "facts_review",
      payload_version: 7,
      view_model: {
        deadline_candidates: [],
        incident_facts: null,
        documents_summary: { total: 0, needs_review: 0, failed: 0 },
      },
      role_affordances: { can_edit: true, can_approve: false, approve_blockers: [] },
    };
    client.setQueryData(gateKey("m1"), envelope);

    // First call: the SUBMIT (409 stale). Then the auto-refetch GETs the fresh envelope.
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(409, { error: "stale_payload_version", fresh_version: 8 }))
      .mockResolvedValueOnce(jsonResponse(200, { ...envelope, payload_version: 8 }));

    const { result } = renderHook(() => useSubmitGate("m1"), { wrapper: wrapper(client) });

    result.current.mutate({
      gate: "facts_review",
      body: { action: "approve", payload_version: 7 },
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    // The error the UI renders is the typed "changed" error.
    expect(result.current.error).toBeInstanceOf(GateStaleError);
    expect((result.current.error as GateStaleError).message).toMatch(/gate changed/i);

    // The refetch happened: a second fetch call, hitting the current-gate endpoint.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/matters/m1/gates/current");
  });

  it("passes a non-stale ApiError straight through (renders verbatim inline)", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(409, {
        error: "guard_failed",
        guard: "deadlines_confirmed",
        code: "deadlines_unconfirmed",
        detail: "SOL / notice-of-claim deadlines are not yet attorney-confirmed",
      }),
    );

    const { result } = renderHook(() => useSubmitGate("m1"), { wrapper: wrapper(client) });
    result.current.mutate({
      gate: "facts_review",
      body: { action: "approve", payload_version: 7 },
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).body.code).toBe("deadlines_unconfirmed");
  });
});
