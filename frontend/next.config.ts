import type { NextConfig } from "next";

/**
 * Dev proxy: the browser talks to the Next dev server (port 3400) at a same-origin
 * `/api/*`, which is rewritten to the FastAPI backend (port 8400). Same-origin keeps
 * the session cookie (`clarionpi_session`, once auth lands) first-party — no CORS, no
 * SameSite juggling. In a real deployment the reverse proxy does this instead.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8400/api/:path*",
      },
    ];
  },
};

export default nextConfig;
