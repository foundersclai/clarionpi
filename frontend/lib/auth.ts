/**
 * Auth client — built against the sibling auth wave's contract, but degrades gracefully
 * until it lands.
 *
 * Contract (parallel wave): `POST /api/auth/login {email,password}` sets cookie
 * `clarionpi_session`; `POST /api/auth/logout` clears it; `GET /api/auth/me` returns a
 * {@link UserView} or 401. At M3-C build time NONE of these endpoints exist yet (the
 * backend still uses the M0 dev-attorney stub with no auth routes), so:
 *   - `me()` treats 401 / 404 / 501 (and network failure) as "logged-out" → resolves null,
 *     NEVER throws. The nav renders "Sign in" and the app stays usable on the dev stub.
 *   - `login()` / `logout()` surface a real ApiError so the login form can render
 *     `invalid_credentials` etc.; a 404/501 (endpoint absent) also surfaces so the form can
 *     say the feature isn't available yet rather than silently "succeeding".
 */

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { LoginResponse, MeView, UserView } from "@/lib/types";

/** Status codes from `me()` that mean "not logged in / auth not wired" — not an error. */
const LOGGED_OUT_STATUSES = new Set([401, 403, 404, 501]);

/**
 * Log in. On success the session cookie is set; returns the user. Throws on refusal.
 * The backend nests the user under `{ user: {...} }`, so we unwrap it here (the caller
 * always wants the user, never the envelope).
 */
export async function login(email: string, password: string): Promise<UserView> {
  const response = await apiPost<LoginResponse>("/api/auth/login", { email, password });
  return response.user;
}

/** Log out. Clears the session cookie. Best-effort: a 404 (no endpoint yet) is swallowed. */
export async function logout(): Promise<void> {
  try {
    await apiPost<unknown>("/api/auth/logout");
  } catch (error) {
    if (error instanceof ApiError && LOGGED_OUT_STATUSES.has(error.status)) {
      return; // endpoint absent or already logged out — nothing to do
    }
    throw error;
  }
}

/**
 * Resolve the current user (with `auth_mode`), or `null` when logged out / auth-not-wired.
 * Never throws for a logged-out condition — the caller treats null as "show Sign in".
 * `me()` returns the user fields plus `auth_mode` at the TOP level (see {@link MeView}).
 */
export async function me(): Promise<MeView | null> {
  try {
    return await apiGet<MeView>("/api/auth/me");
  } catch (error) {
    if (error instanceof ApiError && LOGGED_OUT_STATUSES.has(error.status)) {
      return null;
    }
    // A network error (backend down in dev) should also degrade to logged-out, not crash
    // the shell. Only re-throw genuinely unexpected ApiErrors (e.g. a 500) so they surface.
    if (error instanceof ApiError) {
      throw error;
    }
    return null;
  }
}
