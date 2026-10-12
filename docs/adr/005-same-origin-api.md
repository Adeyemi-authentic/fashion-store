# ADR-005: The browser talks only to the storefront domain (Next.js rewrites to the API)

- **Status:** accepted (2026-10-12)

## Context
The storefront is on Vercel (`*.vercel.app`) and the API is on Render (`*.onrender.com`). These are different sites, so a session cookie set by the API is a **third-party cookie**. Safari blocks these by default and Chrome restricts them. The cart and login would then work in Chrome dev tools and break on a customer's iPhone.

## Decision
- The browser calls only `/api/*` on the storefront's own origin. `next.config` `rewrites()` forwards these to `API_BASE_URL` server to server.
- The API sets cookies as `HttpOnly; Secure; SameSite=Lax; Path=/`. They're first-party because the browser only ever sees the storefront origin.
- No CORS configuration is needed. The API only allows the storefront and server-side callers.
- **Paystack webhooks go straight to the API URL.** They carry no cookies, are authenticated by the HMAC signature, and shouldn't depend on Vercel being up.
- The proxy keeps or sets `X-Request-ID` and passes on `X-Forwarded-For`, so per-IP rate limits see the real client IP. The API trusts that header only from the proxy (shared secret header `X-Proxy-Auth`).
- Checked in Safari (iOS simulator or macOS) as well as Chrome in Phase 2.

## Consequences
- **Good:** cookies work everywhere, no CORS bugs, the API domain is hidden from the browser, and one origin is simpler for CSP.
- **Bad:** each browser API call makes one extra hop (Vercel → Render), adding about 10–50 ms. Acceptable, and cached catalog pages are mostly served by Next.js (ISR) anyway.
- **Bad:** Vercel function limits apply to proxied calls (body size, timeouts). The assistant's streaming responses must be checked through the proxy in Phase 7.

## Alternatives rejected
- **`SameSite=None; Secure` cross-site cookies + CORS with credentials:** blocked by Safari ITP. Fragile.
- **Bearer tokens in `localStorage`:** avoids cookies, but any XSS can steal the token, and guest carts become awkward.
- **Custom domain (`flyfit.example` + `api.flyfit.example`):** also first-party and a fine upgrade if a domain is bought. Not chosen now because of the $0 target.
