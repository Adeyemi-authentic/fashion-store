import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { SignOutButton } from "@/components/account/SignOutButton";
import { getMe } from "@/lib/server-api";

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
      <p className="text-sm text-muted">Order history arrives with checkout in Phase 4.</p>
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
