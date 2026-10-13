# Phase 2: Storefront

**Status:** built, waiting for review. Safari check: see "Done when" below.

## What was built
- **Cart API** (`api/app/modules/cart/`): `GET /cart`, `POST /cart/items`, `PATCH`/`DELETE /cart/items/{variant_id}`. A guest cart is found by a random token in an `HttpOnly; SameSite=Lax` cookie (`Secure` when the site is https). Only a sha256 of the token is stored.
- **`GET /facets?category=`**: the sizes, colours and price range on sale, for the filter dropdowns.
- **Storefront** (`web/`, Next.js 16 + Tailwind 4):
  - home page: hero, category tiles, "New in"
  - `/c/[category]`: sort, size, colour, price band and in-stock filters kept in the URL, plus "Show more" using the keyset cursor
  - `/p/[slug]`: colour and size picker, live stock, "Only N left"
  - cart drawer: change quantity, remove, subtotal, per-line problems
  - a "fictional brand, AI-generated photos" note in the footer and on every product
- **Tests:**
  - 11 cart API tests, including a forced race
  - a test that the app can load all its models on its own
  - a Playwright browse-to-cart flow on a phone-sized Chromium
  - a real-Safari WebDriver check (`npm run e2e:safari`)

## Why, and the trade-offs
- **The cart is not a stock hold.** Adding to the bag checks stock but reserves nothing. Holds start at checkout (Phase 4) and last 15 minutes. Holding stock for every abandoned cart would make items look sold out when nobody is buying them. Cost: an item can sell out while it sits in your bag. The cart then shows "Sold out" or "Only N left" on that line, and checkout stays disabled until it's fixed.
- **One change at a time per cart (`SELECT … FOR UPDATE` on the cart row).** Without it, two quick taps on "Add" both read qty=1 and both write qty=2, or both pass the stock check for the last unit. The test forces that overlap by slowing the read with a short delay. It fails with `[200, 200]` when the lock is removed. That's how I know the test really tests the lock: the first version passed without the lock, because a fast local database never overlapped the requests.
- **Lowering a quantity is always allowed**, even when it's still above stock, so a customer can fix a flagged line one tap at a time. Only increases are checked.
- **Store a hash of the cookie token, not the token.** A database leak (or a backup, or a log of a query) then can't be replayed as live cookies. It costs nothing, because the lookup is by hash anyway.
- **Pages are cached, but stock is live.** Product pages are prerendered at build time and refreshed in the background every 5 minutes (`"use cache"` + a `catalog` cacheLife profile). Stock is the one fast-changing fact, so the product page fetches live counts in the browser on top of the cached page, and the cart API re-checks on every add. A stale page can show a wrong number for a moment, but it can never put more in a bag than exists.
- **Filters live in the URL.** That means a filtered listing can be shared and bookmarked, and the back button works. The checkbox and dropdowns update instantly (`useOptimistic`) while the new results load. Before that fix, the checkbox looked dead for about 1 s.
- **The cart drawer is a native `<dialog>`.** `showModal()` gives focus trapping, Esc to close and an inert background with no library and no hand-written focus code.
- **The browser queues cart changes.** The first add creates the cart and sets the cookie. A second add sent at the same moment would arrive without the cookie and create a second cart, so the client sends changes one after another.

## Decisions made while building
- **Next.js 16 changed caching:** `cacheComponents` is on by default, so caching uses `"use cache"` + `cacheLife` instead of `export const revalidate`. Request-time parts (`params`, `searchParams`) are read inside `<Suspense>`, so the shell is static and only the listing streams.
- **Unknown product or category returns HTTP 200 with the not-found page and a `noindex` tag**, not a 404. Once Next.js starts streaming, the status code is already sent. Search engines still drop the page because of `noindex`. Phase 9 can add a 404 check in `proxy.ts` if real 404s matter for monitoring.
- **Performance:** I inlined the CSS (`experimental.inlineCss`; it's about 6 KB) and load the heading font as a single weight (about 20 KB) instead of the variable font (about 85 KB). That took the home page from 81 to 91 and its layout shift from 0.147 to 0. The shift came from the hero headline re-wrapping when the font arrived.
- **Colour order on the product page** follows `attributes.colours`, whose first entry is the colour in the photo. That way the picker opens on the colour shown, and other colours say "(pictured in X)".
- **Bug found by running the real server:** the first cart write returned a 500 (`NoReferencedTableError`, `carts.customer_id → customers`). Nothing in the app imported the accounts models, and the tests missed it because the seed fixture does import them. Fixed in `main.py`, with a test that loads the app in a fresh Python process.
- **Playwright's WebKit build for macOS 14 crashes on launch here** (bus error), and there's no iOS simulator (Command Line Tools only). The Safari check therefore drives real Safari 17.3 through `safaridriver`.
- **Fixed in passing:** a `mypy` error in the Phase 1 cursor parsing (`datetime.fromisoformat` type).

## Measurements (local production build, API on this machine → Neon in Ohio)
Lighthouse 13, mobile preset (simulated slow 4G, mid-range phone), median of 3 runs:

| Page | Perf | A11y | Best pr. | SEO | LCP | CLS | TBT |
|---|---|---|---|---|---|---|---|
| `/` | 91 | 100 | 100 | 100 | 3.4 s | 0 | 20 ms |
| `/c/adire` | 92 | 100 | 100 | 100 | 3.3 s | 0 | 20 ms |
| `/c/ankara?in_stock=1&sort=price_asc` | 92 | 100 | 100 | 100 | 3.4 s | 0 | 20 ms |
| `/p/adire-agege-kaftan-dress` | 94 | 100 | 100 | 100 | 3.1 s | 0 | 20 ms |

Server response time: prerendered pages 3–8 ms. A filtered listing is about 1.3 s the first time (one API call to Neon from Lagos), then cached. LCP is mostly the simulated slow-4G image download. **These are local numbers.** Re-measure on Vercel in Phase 9.

## Known gaps (on purpose, later phases)
- **Checkout:** the button is disabled until Phase 4.
- **Guest carts are never deleted.** A clean-up job comes in Phase 5 (outbox worker).
- **Guest-to-account cart merge on login:** Phase 3.
- **No rate limit on cart writes yet.** Phase 5 adds the `UNLOGGED` rate-limit table. `X-Proxy-Auth` / `X-Forwarded-For` forwarding comes with it, since nothing reads them before then.
- **The product page shows one image.** The seed has one image per product.
- **A Vercel build needs the API to be up**, because product pages are prerendered from it. The Render free tier sleeps, so the Phase 9 build may need a warm-up request first.

## Done when
- [x] Home, category listing with filters, product page (variant picker, stock shown), cart drawer, mobile-first.
- [x] Server-side cart API using a session cookie, called through the `/api/*` rewrite (ADR-005). Verified in Chrome.
- [ ] Verified in Safari: `npm run e2e:safari`. Needs `safaridriver --enable` once.
- [x] Next.js caching for product pages, image optimisation (AVIF/WebP, responsive sizes).
- [x] Lighthouse 90+ on mobile (91–94), and the full browse-to-cart flow works at phone width (Playwright, Pixel 7).

## 3 interview questions this phase lets me answer
1. **"Two requests add the last unit to the same cart at the same time. What happens?"** Each request locks the cart row (`SELECT … FOR UPDATE`) before reading the line, so the second waits, then sees the first one's write and gets a 409 "Only N left". My test forces the overlap by delaying the read, and fails with both requests succeeding when the lock is removed. Note this protects the cart's own consistency. Real overselling across different customers is stopped at checkout by the conditional `UPDATE` on the variant (Phase 4).
2. **"Your product pages are cached for 5 minutes. How do you avoid showing or selling stock you don't have?"** Split by how fast the data changes. Names, prices and images can be 5 minutes stale, so they're prerendered and served from the cache in milliseconds. Stock is fetched live on top of the page, and the server re-checks it on every cart change. The cache is never trusted for a decision, only for display.
3. **"Why does the browser call `/api` on your own domain instead of the API's domain?"** So the cart cookie is first-party. A cookie set by `onrender.com` while you're on `vercel.app` is a third-party cookie: Safari blocks it, so the cart would work in Chrome and silently break on iPhones. A Next.js rewrite forwards `/api/*` server to server. That costs one extra hop (about 10–50 ms), but cookies work everywhere and no CORS setup is needed.
