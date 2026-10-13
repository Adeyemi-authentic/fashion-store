# web

Next.js 16 (App Router, Cache Components) + TypeScript + Tailwind 4 storefront.

The browser only calls `/api/*` on this domain; `next.config.ts` rewrites those requests to the FastAPI app (ADR-005), so the cart cookie is first-party. Server components call the API directly with `API_BASE_URL`. Config is read from the repo-root `.env`.

```bash
npm install
npm run dev                 # http://localhost:3000 (API must be running on :8000)
npm run build && npm start  # production build; product pages are prerendered from the API
npm run typecheck && npm run lint
npm run e2e                 # Playwright, phone-sized Chromium + WebKit, against `npm start`
```

## Caching
- Catalog data (`lib/api.ts`) is cached with `"use cache"` + the `catalog` cacheLife profile: served from cache, refreshed in the background after 5 minutes.
- Product pages are prerendered at build time for every active product; new slugs render on first visit and are then cached.
- Live stock on the product page is fetched in the browser on top of the cached page, and the cart API re-checks stock on every add, so a stale page can't oversell.

## Layout
```
app/          layout (header, category nav, cart drawer), home, /c/[category], /p/[slug]
components/   ProductCard, Filters, LoadMore, VariantPicker, cart/ (provider, button, drawer)
lib/          api.ts (server-only cached fetchers), catalog.ts (labels, filters), types, format
e2e/          Playwright browse-to-cart flow
```
