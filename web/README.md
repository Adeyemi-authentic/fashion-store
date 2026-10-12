# web

Next.js (App Router) + TypeScript + Tailwind storefront. Scaffolded in Phase 2.

The browser only calls `/api/*` on this domain; `next.config` rewrites forward those requests to the FastAPI app (see ADR-005).
