import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { SignOutButton } from "@/components/account/SignOutButton";
import { StatusBadge } from "@/components/admin/StatusBadge";
import { formatNaira } from "@/lib/format";
import { apiAs, getMe } from "@/lib/server-api";

type MyOrder = { reference: string; status: string; total_kobo: number; created_at: string; items: { name: string; qty: number }[] };
const dateFmt = new Intl.DateTimeFormat("en-NG", { dateStyle: "medium", timeZone: "Africa/Lagos" });

export const metadata: Metadata = { title: "Your account", robots: { index: false } };

export default function AccountPage() {
  return (
    <div className="mx-auto max-w-xl px-4 pt-10">
      <h1 className="font-display text-3xl">Your account</h1>
      <Suspense fallback={<div className="mt-6 h-24" />}>
        <Details />
      </Suspense>
    </div>
  );
}

async function Details() {
  const me = await getMe();
  if (!me) redirect("/account/login");
  return (
    <div className="mt-6 space-y-6">
      <p>
        Signed in as <span className="font-semibold">{me.email}</span>
      </p>
      <Orders />
      <div className="flex flex-wrap gap-3">
        {me.role === "admin" && (
          <Link href="/admin" className="rounded-full bg-ink px-5 py-2.5 text-sm font-semibold text-cream">
            Admin
          </Link>
        )}
        <SignOutButton />
      </div>
    </div>
  );
}

async function Orders() {
  const result = await apiAs<MyOrder[]>("/orders/mine");
  const orders = result.ok ? result.data : [];
  return (
    <section>
      <h2 className="font-semibold">Orders</h2>
      {orders.length === 0 ? (
        <p className="mt-2 text-sm text-muted">No orders yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line rounded-lg border border-line bg-white text-sm" data-testid="my-orders">
          {orders.map((o) => (
            <li key={o.reference}>
              <Link href={`/checkout/callback?reference=${o.reference}`} className="flex items-center gap-3 p-3 hover:bg-cream">
                <div className="min-w-0 flex-1">
                  <p className="truncate">{o.items.map((i) => `${i.name} × ${i.qty}`).join(", ")}</p>
                  <p className="text-xs text-muted">{dateFmt.format(new Date(o.created_at))}</p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <span>{formatNaira(o.total_kobo)}</span>
                  <StatusBadge status={o.status} />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
