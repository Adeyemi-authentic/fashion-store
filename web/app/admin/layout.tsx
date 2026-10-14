import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { getMe } from "@/lib/server-api";

export const metadata: Metadata = { title: "Admin", robots: { index: false } };

export default function AdminLayout({ children }: LayoutProps<"/admin">) {
  return (
    <div className="mx-auto max-w-6xl px-4 pt-6">
      <nav aria-label="Admin" className="flex flex-wrap items-center gap-1 border-b border-line pb-3 text-sm">
        <span className="mr-3 font-semibold">Admin</span>
        <Link href="/admin" className="rounded-full px-3 py-2 hover:bg-sand">Products</Link>
        <Link href="/admin/products/new" className="rounded-full px-3 py-2 hover:bg-sand">New product</Link>
        <Link href="/admin/orders" className="rounded-full px-3 py-2 hover:bg-sand">Orders</Link>
        <Link href="/admin/descriptions" className="rounded-full px-3 py-2 hover:bg-sand">Descriptions</Link>
      </nav>
      <Suspense fallback={<p className="py-10 text-muted">Loading…</p>}>
        <Gate>{children}</Gate>
      </Suspense>
    </div>
  );
}

// Keeps customers out of the admin screens. This is for a sensible UI only: the API checks
// the role on every admin request, so skipping this page gets nobody anything.
async function Gate({ children }: { children: React.ReactNode }) {
  const me = await getMe();
  if (!me) redirect("/account/login?next=/admin");
  if (me.role !== "admin") {
    return (
      <div className="py-16 text-center">
        <h1 className="font-display text-2xl">Admins only</h1>
        <p className="mt-2 text-muted">You&apos;re signed in as {me.email}, which isn&apos;t an admin account.</p>
      </div>
    );
  }
  return <div className="py-6">{children}</div>;
}
