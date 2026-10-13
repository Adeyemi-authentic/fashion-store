import type { Metadata } from "next";
import { Suspense } from "react";
import { OrderStatus } from "@/components/checkout/OrderStatus";

export const metadata: Metadata = { title: "Your order", robots: { index: false } };

// Paystack sends the customer here with ?reference=... (and ?trxref=...).
export default function CallbackPage(props: PageProps<"/checkout/callback">) {
  return (
    <div className="mx-auto max-w-xl px-4 pt-8">
      <h1 className="font-display text-3xl">Your order</h1>
      <Suspense fallback={<p className="mt-6 text-muted">Checking your payment…</p>}>
        <Status searchParams={props.searchParams} />
      </Suspense>
    </div>
  );
}

async function Status({ searchParams }: Pick<PageProps<"/checkout/callback">, "searchParams">) {
  const sp = await searchParams;
  const reference = typeof sp.reference === "string" ? sp.reference : typeof sp.trxref === "string" ? sp.trxref : "";
  if (!reference) return <p className="mt-6">No order reference in this link.</p>;
  return <OrderStatus reference={reference} />;
}
