"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useCart } from "@/components/cart/CartProvider";
import { formatNaira } from "@/lib/format";
import { newIdempotencyKey } from "@/lib/idempotency";
import { useHydrated } from "@/lib/useHydrated";

const MAX_BUSY_RETRIES = 5;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function CheckoutForm({ accountEmail }: { accountEmail: string | null }) {
  const { cart, refresh } = useCart();
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per checkout attempt: a double-tap, a retry after a dropped connection, or a
  // "still processing" 409 all reuse it, so the API creates at most one order. A changed bag
  // is a new attempt and gets a new key.
  const key = useRef<string | null>(null);
  const bagSignature = cart ? cart.items.map((l) => `${l.variant_id}:${l.qty}`).join(",") : "";
  useEffect(() => {
    key.current = null;
  }, [bagSignature]);

  async function pay(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    key.current ??= newIdempotencyKey();
    try {
      for (let attempt = 0; attempt <= MAX_BUSY_RETRIES; attempt++) {
        const res = await fetch("/api/checkout", {
          method: "POST",
          headers: { "content-type": "application/json", "Idempotency-Key": key.current },
          body: JSON.stringify(accountEmail ? {} : { email }),
        });
        const body = await res.json().catch(() => null);
        if (res.status === 201) {
          window.location.assign(body.authorization_url); // Paystack's hosted page
          return;
        }
        if (res.status === 409 && typeof body?.detail === "string") {
          // The same attempt is still being processed (e.g. a double-tap): wait and ask again.
          await sleep(Number(res.headers.get("retry-after") ?? 1) * 1000);
          continue;
        }
        if (res.status === 409) {
          setError(body?.detail?.message ?? "Some items just sold out.");
          key.current = null;
          await refresh(); // show which lines changed
        } else if (res.status === 422) {
          setError(typeof body?.detail === "string" ? body.detail : "Check your email address.");
        } else {
          setError(typeof body?.detail === "string" ? body.detail : "Something went wrong. Please try again.");
        }
        break;
      }
    } catch {
      setError("Can't reach the shop right now. Check your connection and try again.");
    }
    setBusy(false);
  }

  if (!cart) return <p className="mt-6 text-muted">Loading your bag…</p>;
  if (cart.items.length === 0) {
    return (
      <div className="mt-6">
        <p className="text-muted">Your bag is empty.</p>
        <Link href="/" className="mt-4 inline-flex rounded-full border border-ink px-5 py-2.5 text-sm font-medium">
          Start shopping
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={pay} className="mt-6 space-y-6">
      <ul className="divide-y divide-line rounded-lg border border-line bg-white" data-testid="checkout-lines">
        {cart.items.map((l) => (
          <li key={l.variant_id} className="flex gap-3 p-3 text-sm">
            {l.image && (
              <Image src={l.image.url} alt={l.image.alt} width={48} height={72} quality={70} className="h-[72px] w-12 rounded bg-sand object-cover" />
            )}
            <div className="min-w-0 flex-1">
              <p className="font-medium">{l.product_name}</p>
              <p className="text-muted">{l.colour} · {l.size} · × {l.qty}</p>
              {l.problem && <p className="text-clay">{l.problem === "not_enough_stock" ? `Only ${l.available} left` : "Not available"}</p>}
            </div>
            <span className="shrink-0">{formatNaira(l.line_total_kobo)}</span>
          </li>
        ))}
      </ul>
      <div className="flex items-baseline justify-between text-lg">
        <span>Total</span>
        <span className="font-semibold" data-testid="checkout-total">{formatNaira(cart.subtotal_kobo)}</span>
      </div>

      {accountEmail ? (
        <p className="text-sm text-muted">Receipt goes to {accountEmail}.</p>
      ) : (
        <label className="block text-sm">
          Email for your receipt
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 h-12 w-full rounded-lg border border-line bg-white px-3 text-base focus:border-ink focus:outline-none"
          />
          <span className="mt-1 block text-xs text-muted">
            Or <Link href="/account/login?next=/checkout" className="underline">sign in</Link> to keep your order history.
          </span>
        </label>
      )}

      {error && <p className="text-sm text-clay" role="alert">{error}</p>}
      <button
        type="submit"
        disabled={!hydrated || busy || !cart.can_checkout}
        className="h-12 w-full rounded-full bg-ink text-sm font-semibold text-cream disabled:opacity-50"
      >
        {busy ? "Starting payment…" : `Pay ${formatNaira(cart.subtotal_kobo)} with Paystack`}
      </button>
      <p className="text-center text-xs text-muted">
        Test mode: no real money moves. Your items are held for 15 minutes while you pay.
      </p>
    </form>
  );
}
