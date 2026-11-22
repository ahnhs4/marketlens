import type { NextConfig } from "next";

// The browser only ever talks to this Next.js origin; /api/* is proxied to the Python
// backend so API keys and provider credentials stay server-side and cookies stay first-party.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

// NEXT_PUBLIC_STATIC=1 builds the read-only demo: a static export (GitHub Pages) that reads API answers from
// public/snapshot instead of the backend, served under NEXT_PUBLIC_BASE_PATH.
const STATIC = process.env.NEXT_PUBLIC_STATIC === "1";

const staticConfig: NextConfig = {
  output: "export",
  distDir: "out-static",          // separate from .next so a running dev server is untouched
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || undefined,
  trailingSlash: true,
  images: { unoptimized: true },
};

const nextConfig: NextConfig = STATIC ? staticConfig : {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
