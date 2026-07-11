import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQuery } from "../test-utils";
import { DocumentsPanel } from "@/components/documents-panel";

/** Read a Blob/File through FileReader — the one Blob-reading API jsdom fully supports. */
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The registration response with slots deliberately OUT of registration order. */
function shuffledSession(matterId: string) {
  return {
    id: "sess-1",
    matter_id: matterId,
    status: "open",
    ttl_expires_at: "2099-01-01T00:00:00Z",
    // Registration order was [a.pdf, b.pdf]; the backend returns [b.pdf, a.pdf].
    slots: [
      {
        id: "slot-b",
        filename: "b.pdf",
        size_bytes: 5,
        received: false,
        upload_url: "/api/uploads/slots/slot-b",
      },
      {
        id: "slot-a",
        filename: "a.pdf",
        size_bytes: 5,
        received: false,
        upload_url: "/api/uploads/slots/slot-a",
      },
    ],
  };
}

/** Route the panel's fetches; records every PUT (url → body text) for pairing assertions. */
function mockFetchRoutes(puts: Array<{ url: string; body: string }>) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "GET" && url.includes("/documents")) {
        return jsonResponse(200, { documents: [] });
      }
      if (method === "GET" && url.includes("/dedup")) {
        return jsonResponse(200, { decisions: [] });
      }
      if (method === "POST" && url.endsWith("/uploads")) {
        return jsonResponse(201, shuffledSession("m1"));
      }
      if (method === "PUT") {
        const body = init?.body as BodyInit | undefined;
        const text =
          typeof body === "string"
            ? body
            : body instanceof Blob
              ? await readBlob(body)
              : String(body ?? "");
        puts.push({ url, body: text });
        return jsonResponse(200, {
          id: url.split("/").pop(),
          filename: "x",
          size_bytes: 5,
          received: true,
          upload_url: null,
        });
      }
      if (method === "POST" && url.includes("/commit")) {
        return jsonResponse(201, { session_id: "sess-1", documents: [] });
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DocumentsPanel upload slot pairing", () => {
  it("diagnostic: shuffled slots mispair under index-based matching (BUS-06 evidence)", async () => {
    const puts: Array<{ url: string; body: string }> = [];
    mockFetchRoutes(puts);
    const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});

    renderWithQuery(<DocumentsPanel matterId="m1" />);

    const input = screen.getByLabelText("Choose files to upload");
    const fileA = new File(["AAAAA"], "a.pdf", { type: "application/pdf" });
    const fileB = new File(["BBBBB"], "b.pdf", { type: "application/pdf" });
    await userEvent.upload(input, [fileA, fileB]);

    await waitFor(() => expect(puts).toHaveLength(2));

    // The diagnostic must report the mispair: browser file 0 (a.pdf) was paired with
    // slot-b (b.pdf) — filename_matches false for BOTH indexes under index pairing.
    const pairingCalls = debugSpy.mock.calls.filter(
      (call) => call[0] === "clarionpi.uploads.pairing",
    );
    expect(pairingCalls).toHaveLength(2);
    expect(pairingCalls[0][1]).toEqual({
      browser_file_index: 0,
      slot_id: "slot-b",
      filename_matches: false,
    });
    expect(pairingCalls[1][1]).toEqual({
      browser_file_index: 1,
      slot_id: "slot-a",
      filename_matches: false,
    });

    // And the bytes really do land under the WRONG declared identity: a.pdf's bytes go
    // to b.pdf's slot. Same byte length both sides — counts alone cannot catch this.
    const putByUrl = new Map(puts.map((p) => [p.url, p.body]));
    expect(putByUrl.get("/api/uploads/slots/slot-b")).toBe("AAAAA");
    expect(putByUrl.get("/api/uploads/slots/slot-a")).toBe("BBBBB");
  });
});
