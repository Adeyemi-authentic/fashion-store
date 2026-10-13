"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useCart } from "@/components/cart/CartProvider";
import { formatNaira } from "@/lib/format";

type Order = {
  reference: string;
  status: string;
  total_kobo: number;
  items: { name: string; sku: string; qty: number; unit_price_kobo: number }[];
};

const SETTLED = new Set(["paid", "fulfilled", "refund_pending", "refunded", "under_review"]);
const POLL_MS = 2000;
const MAX_POLLS = 15; // ~30 s; the webhook or our verify call normally settles it in 1-2

/** Asks the API (which asks Paystack, never trusting this page's URL) until the order settles. */
export function OrderStatus({ reference }: { reference: string }) {
  const { refresh } = useCart();
  const [order, setOrder] = useState<Order | null>(null);
  const [missing, setMissing] = useState(false);
  const [gaveUp, setGaveUp] = useState(false);

  useEffect(() => {
    let stop = false;
    (async () => {
      for (let i = 0; i < MAX_POLLS && !stop; i++) {
        const res = await fetch(`/api/orders/by-reference/${encodeURIComponent(reference)}`, { cache: "no-store" }).catch(() => null);
        if (res?.status === 404) return setMissing(true);
        if (res?.ok) {
          const o = (await res.json()) as Order;
          setOrder(o);
          if (SETTLED.has(o.status)) {
            if (o.status === "paid") void refresh(); // bought items have left the bag
            return;
          }
        }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
      if (!stop) setGaveUp(true);
    })();
    return () => {
      stop = true;
    };
  }, [reference, refresh]);

  if (missing) return <p className="mt-6">We couldn&apos;t find that order.</p>;
  if (!order) return <p className="mt-6 text-muted" role="status">Checking your payment…</p>;

  const message: Record<string, [string, string]> = {
    paid: ["Payment received. Thank you!", "A confirmation email is on its way."],
    fulfilled: ["Your order is on its way", ""],
    refund_pending: [
      "Sorry, we couldn't complete this order",
      "Your payment arrived after your items were held for someone else, so it is being refunded in full. We've emailed you.",
    ],
    refunded: ["This order was refunded", ""],
    under_review: ["We're checking your payment", "Something didn't match. We'll email you within a day."],
    pending_payment: gaveUp
      ? ["We haven't received your payment yet", "If you paid, it can take a minute. Refresh this page to check again."]
      : ["Checking your payment…", ""],
    expired: ["This checkout timed out", "Your items were released. You can check out again from your bag."],
    cancelled: ["This order was cancelled", ""],
  };
  const [title, detail] = message[order.status] ?? ["Order status: " + order.status, ""];

  return (
    <div className="mt-6 space-y-5" data-testid="order-status" data-status={order.status}>
      <div>
        <h2 className="font-display text-2xl" role="status">{title}</h2>
        {detail && <p className="mt-1 text-muted">{detail}</p>}
      </div>
      <ul className="divide-y divide-line rounded-lg border border-line bg-white text-sm">
        {order.items.map((i) => (
          <li key={i.sku} className="flex justify-between gap-3 p-3">
            <span>{i.name} × {i.qty}</span>
            <span>{formatNaira(i.unit_price_kobo * i.qty)}</span>
          </li>
        ))}
        <li className="flex justify-between p-3 font-semibold">
          <span>Total</span>
          <span>{formatNaira(order.total_kobo)}</span>
        </li>
      </ul>
      <p className="text-xs text-muted">Reference {order.reference}</p>
      <Link href="/" className="inline-flex rounded-full border border-ink px-5 py-2.5 text-sm font-medium">
        Continue shopping
      </Link>
    </div>
  );
}
