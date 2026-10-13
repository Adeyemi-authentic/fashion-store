import type { Metadata } from "next";
import { Suspense } from "react";
import { CheckoutForm } from "@/components/checkout/CheckoutForm";
import { getMe } from "@/lib/server-api";

export const metadata: Metadata = { title: "Checkout", robots: { index: false } };

export default function CheckoutPage() {
  return (
    <div className="mx-auto max-w-xl px-4 pt-8">
      <h1 className="font-display text-3xl">Checkout</h1>
      <Suspense fallback={<p className="mt-6 text-muted">Loading…</p>}>
        <WithAccount />
      </Suspense>
    </div>
  );
}

async function WithAccount() {
  const me = await getMe();
  return <CheckoutForm accountEmail={me?.email ?? null} />;
}
