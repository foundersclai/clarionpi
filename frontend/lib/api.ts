/**
 * Typed fetch wrapper — the single door to the backend REST surface.
 *
 * Every call sends `credentials: "include"` so the session cookie (`clarionpi_session`,
 * once auth lands) rides along; requests are same-origin `/api/*` in dev via the Next
 * rewrite. On a non-2xx the wrapper throws {@link ApiError}, whose `body.error` is the
 * backend's typed refusal code (`jurisdiction_unsupported`, `matter_not_found`,
 * `upload_incomplete`, `role_forbidden`, `unauthenticated`, `invalid_credentials`, ...).
 * Screens render `body.error` inline — the frontend never invents an error string.
 */

/** The typed error body the backend returns on a refusal (all fields optional/extra). */
export interface ApiErrorBody {
  error?: string;
  detail?: string;
  [key: string]: unknown;
}

/** Thrown on any non-2xx response. `status` + `body.error` are the render inputs. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error ?? body.detail ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Parse a response body as JSON, tolerating an empty body (204/empty 2xx). */
async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text.length === 0) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    // A non-JSON body (e.g. an HTML error page from a proxy) — surface it as a detail.
    return { detail: text };
  }
}

/**
 * Normalize a parsed error body to the flat `{error, detail}` contract every renderer trusts.
 *
 * Two shapes reach the client: route refusals are flat (`{error, detail}`), but the auth
 * dependency raises `HTTPException(detail={"error": "unauthenticated"})`, which FastAPI serializes
 * NESTED as `{detail: {error: "unauthenticated"}}`. Left as-is, a renderer doing
 * `body.error ?? body.detail` gets the raw `{error}` OBJECT and crashes React ("Objects are not
 * valid as a React child"). Flattening here — the single ingestion point — lifts the nested code up
 * to `error`, keeps any sibling fields (e.g. `actual`, `guard`), and guarantees `detail` is a
 * string, so no downstream consumer can render an object.
 */
function normalizeErrorBody(parsed: unknown): ApiErrorBody {
  if (parsed === null || typeof parsed !== "object") {
    return { detail: String(parsed) };
  }
  const body = parsed as Record<string, unknown>;
  const detail = body.detail;
  if (body.error === undefined && detail !== null && typeof detail === "object") {
    const inner = detail as Record<string, unknown>;
    const code = typeof inner.error === "string" ? inner.error : undefined;
    // Lift the nested code + sibling fields to the top level; `body` (sans its object `detail`)
    // still wins for any real top-level keys.
    return {
      ...inner,
      ...body,
      error: code,
      detail: code ?? JSON.stringify(detail),
    };
  }
  return body as ApiErrorBody;
}

/** Shared response handler: 2xx → typed body; else throw {@link ApiError}. */
async function handle<T>(response: Response): Promise<T> {
  const parsed = await parseJson(response);
  if (!response.ok) {
    throw new ApiError(response.status, normalizeErrorBody(parsed));
  }
  return parsed as T;
}

/** GET `path`, returning the typed JSON body. Throws {@link ApiError} on non-2xx. */
export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  return handle<T>(response);
}

/** POST `body` (JSON) to `path`, returning the typed JSON body. */
export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return handle<T>(response);
}

/**
 * PUT raw bytes to `url` (the slot upload URL the sessions layer hands us). Not JSON —
 * the backend reads `await request.body()`. Returns the parsed JSON slot view.
 */
export async function apiPutBytes<T>(url: string, data: Blob): Promise<T> {
  const response = await fetch(url, {
    method: "PUT",
    credentials: "include",
    body: data,
  });
  return handle<T>(response);
}
