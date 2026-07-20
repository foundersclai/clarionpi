import type { NextConfig } from "next";

/**
 * Dev proxy: the browser talks to the Next dev server at a same-origin `/api/*`, which is
 * rewritten to the FastAPI backend. Same-origin keeps the session cookie
 * (`clarionpi_session`) first-party — no CORS, no SameSite juggling. In a real deployment
 * the reverse proxy does this instead.
 *
 * The destination defaults to the standard dev backend (`http://127.0.0.1:8400`, paired with
 * `npm run dev` on 3400). `CLARIONPI_BACKEND_ORIGIN` overrides it so the workshop demo can
 * point the 3001 frontend at the 8001 backend (`make workshop-*`) with no code edit.
 */
const backendOrigin = process.env.CLARIONPI_BACKEND_ORIGIN ?? "http://127.0.0.1:8400";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
