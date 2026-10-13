import path from "node:path";
import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

// One .env at the repo root serves both apps (see .env.example). On Vercel the values come
// from the project settings instead and this finds no file, which is fine.
loadEnvConfig(path.resolve(process.cwd(), ".."));

const apiBase = process.env.API_BASE_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Tailwind CSS for the whole site is ~6 KB, so it goes inline in <head> instead of being a
  // render-blocking request. Costs ~6 KB per HTML page for returning visitors.
  experimental: { inlineCss: true },
  cacheLife: {
    // Product names, prices and listings. Stock on the product page is fetched live on top
    // of this, and the cart API re-checks stock, so a few minutes of staleness is safe.
    catalog: { stale: 60, revalidate: 300, expire: 86400 },
  },
  images: {
    qualities: [70],
    formats: ["image/avif", "image/webp"],
    localPatterns: [{ pathname: "/images/**", search: "" }],
  },
  // ADR-005: the browser only ever calls /api/* on this origin. Next.js forwards it to the
  // API server to server, so the API's cookies are first-party and no CORS is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiBase}/:path*` }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
