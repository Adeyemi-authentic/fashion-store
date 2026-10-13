# Phase 3: Accounts and admin

**Status:** built and tested, waiting for review.

## What was built
- **Accounts API** (`api/app/modules/accounts/`): `POST /auth/signup`, `/auth/login`, `/auth/logout`, `GET /auth/me`.
  - Passwords are hashed with argon2id.
  - Sessions are server-side: a random token sits in an `HttpOnly; SameSite=Lax` cookie, and only its sha256 is stored in `sessions`.
- **Customer carts:** a signed-in customer always uses their own cart. At login or sign-up, the guest cart is merged into it inside the login transaction.
- **Admin API** (`api/app/modules/admin/`, owns no tables):
  - list products (search, status, keyset pages), create, edit, archive
  - add variants, set prices
  - stock adjustments as +/− deltas
  - order list filtered by status
- **`scripts/make_admin.py`** is the only way to give someone the admin role. There's no API route for it.
- **Storefront:**
  - sign-in, sign-up and account pages, plus an account icon in the header
  - `/admin`: products, new product, product editor (details, status, image, variant prices, stock), orders
- **Tests:**
  - 29 new API tests, 74 in total
  - 2 new Playwright journeys (customer and admin), which clean up their test data on Neon afterwards
  - the real-Safari cart check re-run and passing

## Why, and the trade-offs
- **Server-side sessions, not JWTs.** Logout deletes a row, so the token is dead at once, including copies of it. With a JWT, logout only removes the browser's copy, and a stolen token keeps working until it expires. Cost: one indexed lookup per request. The same query already loads the customer's role, so admin checks need nothing extra.
- **A new session token on every login (session fixation).** If an attacker plants a known token in someone's browser before they log in, it's worthless afterwards. The old session row is deleted in the same transaction.
- **One error for "no such email" and "wrong password", with equal timing.** For an unknown email, the API still verifies the password against a dummy argon2 hash, so the response takes as long as a real attempt. Neither the message nor the timing tells an attacker which emails have accounts.
- **argon2 runs on a worker thread.** Each hash takes about 50 ms of CPU. On the event loop, it would pause every other request during a login. Passwords are capped at 128 characters, so nobody can send a megabyte to hash.
- **CSRF protection is `SameSite=Lax` plus JSON-only bodies.** Browsers don't send Lax cookies on another site's POST, and a plain HTML form can't send `application/json`. That's enough without a CSRF token. Server Actions also check that the request comes from this site (Next.js compares `Origin` with `Host`).
- **The admin check sits on the router** (`APIRouter(dependencies=[Depends(require_admin)])`), so a route added later is protected even if its author forgets. The test reads every `/admin` route from the OpenAPI schema and checks 401 for visitors and 403 for customers, so new routes are covered without editing the test. Removing the router-level check made 3 routes fail the test. The other 6 still passed, because they also take the admin as a parameter (to log who changed what), so they're checked twice.
- **The admin UI hides things, but the API decides.** The `/admin` layout shows "Admins only" to customers, but that's cosmetic. The e2e test also calls `/api/admin/products` directly as a customer and gets 403.
- **Admin edits are Server Actions that forward the caller's cookie.** Next.js docs warn that Server Actions are public POST endpoints. These actions trust nothing about the caller: the API makes the 403 decision. The storefront cache is refreshed with `updateTag("products")` only after the API accepts the change. `updateTag` rather than `revalidateTag`, so the next view waits for fresh data instead of serving the old page once more. The e2e test checks that a newly published product appears on the cached category page immediately.
- **Cart merge rules.** If the same variant is in both carts, the quantities add up, capped at 10 per line. Stock isn't checked during the merge: lines over stock show the usual "Only N left" flag. The merge runs inside the login transaction, so login and merge succeed or fail together. Locks are taken in a fixed order (customer cart, then guest cart), so two logins at once can't deadlock. The guest cart is deleted, not left behind.
- **First add for a signed-in customer uses `INSERT … ON CONFLICT DO NOTHING`.** Two requests at once can't both create a cart (`carts.customer_id` is UNIQUE). There's a test that fires two adds at once and expects one cart with two lines.
- **Stock changes are deltas in one conditional `UPDATE`.** Reading the number and writing a new total would lose a sale that happens in between. The `UPDATE` applies `+delta` to whatever the row holds right then, and refuses (409) if on-hand would fall below what's reserved for unpaid orders. All stock writes are still in `orders.inventory` (ADR-003). Creating a product writes its catalog rows and its starting stock (via inventory) in one transaction.
- **Archive, never delete.** Phase 4 orders will point at variants, so rows stay. Archived products disappear from the shop and stay in admin.
- **Slugs never change** after creation, even when the product is renamed. URLs, bookmarks and cache tags all use the slug.

## Decisions made while building
- **Admin role only from the shell** (`make_admin.py`). With no "make admin" route, there's no route to get wrong.
- **Publishing needs at least one variant and an image.** That matches the Phase 1 rule that products without images stay drafts.
- **Cookie flags live in one place** (`app/shared/cookies.py`), so the session and cart cookies can't drift apart, and the accounts module doesn't import the cart's router.
- **New import-linter contract:** accounts and admin may only use other modules through their `service`. There are now 3 contracts, all kept.
- **Bug found by e2e: uvicorn keep-alive.** Under load, the Next.js `/api` proxy got `ECONNRESET` ("socket hang up") and returned 500. Uvicorn closes idle connections after 5 s, and the proxy sometimes reused one just as it closed. Fixed by running uvicorn with `--timeout-keep-alive 75` (the server must keep connections longer than the proxy does). This is now in `api/README.md`. **Phase 9: put it in the Render start command.**
- **This FastAPI version stores included routers as wrapper objects** (`app.routes` doesn't list `/admin/...`). The first version of the 403 test therefore found **zero** routes and passed without checking anything. The guard test caught it (it asserts at least 9 routes), and routes now come from the OpenAPI schema.
- **`email-validator` rejects reserved domains like `.test`.** Test accounts use `e2e-…@example.com`, and `scripts/e2e_cleanup.py` deletes them and any "E2E …" products after the browser tests.
- **Clearer stock error:** "Can't remove more than the 5 on hand" when nothing is held, and "Can't go below 7: that many are held for unpaid orders" when something is.
- **Order list** is built and tested with inserted rows, but stays empty until Phase 4 creates orders.

## Measurements
- **API tests:** 74 passing in about 6 s. ruff, mypy and import-linter are clean.
- **e2e (Pixel 7 Chromium):** 4 journeys passing, twice in a row. The real-Safari cart check passes.
- **Admin saves take about 6 s from here.** A create is around 25 database round trips at about 250 ms each (Lagos → Neon in Ohio). The e2e tests allow 30 s per save for this reason. In production the API runs next to Neon (about 1 ms per round trip), and Phase 8 can cut the round trips if it's still slow.
- **Neon after the tests:** back to exactly 131 active and 19 draft products, with no test accounts left.

## Known gaps (on purpose, later phases)
- **No rate limit on login, sign-up or cart writes yet.** Phase 5 adds the `UNLOGGED` rate-limit table (per IP via `X-Forwarded-For` from the proxy, and per account). Until then, password guessing is slowed only by argon2's cost.
- **No password reset or email verification.** Out of scope for the concept build; noted for the README's "what I'd do next".
- **No admin audit table.** Admin changes are logged with who made them (`log.info`), not stored in the database. Phase 5's structured logs make those searchable.
- **No image upload.** The admin picks a path under `/images/products/`, and the API accepts only that pattern.
- **Order history on the account page** comes with Phase 4.

## Done when
- [x] Sign-up and login (argon2 hashes, HttpOnly session cookies). Guest checkout stays possible (guests keep their cookie cart). The cart is merged at login.
- [x] Admin: product CRUD (archive as delete), stock adjustment, order list with status filter.
- [x] Role checks enforced on the API: a customer gets 403 on **every** admin route (tested), and a visitor gets 401.
- [x] The cart merge is tested: quantities add and are capped, both devices see the same cart, the guest cart is deleted and its cookie cleared, and the test fails if the merge is removed.

## 3 interview questions this phase lets me answer
1. **"How do you make sure nobody can reach admin endpoints without the role?"** The check sits on the admin router itself, so every route under `/admin` gets it, including ones added later. A parametrised test reads every admin route from the OpenAPI schema and expects 401 for visitors and 403 for customers, so a new route is tested automatically. The UI hides admin links too, but only for looks: my e2e test calls the admin API directly as a customer and gets 403. Story to tell: the first version of that test found zero routes, because FastAPI changed how it stores included routers, and it passed while checking nothing. A guard test caught it.
2. **"Sessions or JWTs?"** Server-side sessions, for logout and revocation. Deleting a row ends a session immediately. A JWT stays valid until it expires, so a stolen one works until then. The cost is one indexed lookup per request, which also loads the role. The cookie holds a random token, and the database stores only its hash, so a leaked table can't be replayed. A new token on every login prevents session fixation.
3. **"A guest has items in their bag and logs in to an account that has its own cart. What happens?"** The guest cart is merged into the account cart in the same transaction as the login. Quantities for the same item add up, capped at 10 per line. Stock isn't re-checked during the merge, because the cart flags over-stock lines anyway. The guest cart is deleted and its cookie cleared. Locks are taken customer-cart-first in every merge, so two logins at once can't deadlock.
