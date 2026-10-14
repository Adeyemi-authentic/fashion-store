import type { Metadata, Viewport } from "next";
import { Fraunces, Inter } from "next/font/google";
import Link from "next/link";
import { Suspense } from "react";
import { Assistant } from "@/components/assistant/Assistant";
import { CartButton } from "@/components/cart/CartButton";
import { CartDrawer } from "@/components/cart/CartDrawer";
import { CartProvider } from "@/components/cart/CartProvider";
import { getCategories, POLICY_PAGES } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { categoryLabel } from "@/lib/catalog";
import "./globals.css";

const body = Inter({ variable: "--font-body", subsets: ["latin"] });
// One static weight (~20 KB) instead of the variable font (~85 KB): the headline is the
// largest thing on the home page, so its font is on the LCP path.
const heading = Fraunces({ variable: "--font-heading", subsets: ["latin"], weight: "500" });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: { default: `${BRAND}: Lagos fashion (concept store)`, template: `%s · ${BRAND}` },
  description: `${BRAND} is a fictional Lagos fashion brand: a concept build with AI-generated product images. Nothing here is for sale.`,
};

export const viewport: Viewport = { themeColor: "#faf6ef" };

async function CategoryNav() {
  const categories = await getCategories();
  return (
    <>
      {categories.map((c) => (
        <Link
          key={c.slug}
          href={`/c/${c.slug}`}
          className="shrink-0 rounded-full px-3 py-2 text-sm text-muted hover:bg-sand hover:text-ink"
        >
          {categoryLabel(c.slug)}
        </Link>
      ))}
    </>
  );
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en-NG" className={`${body.variable} ${heading.variable} antialiased`}>
      <body className="flex min-h-dvh flex-col font-sans">
        <CartProvider>
          <header className="sticky top-0 z-30 border-b border-line bg-cream/95 backdrop-blur">
            <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
              <Link href="/" className="font-display text-2xl tracking-tight">
                {BRAND}
              </Link>
              <div className="flex items-center">
                {/* A plain GET form: works before any JavaScript has loaded. */}
                <form action="/search" role="search" className="mr-1 hidden md:block">
                  <label htmlFor="header-q" className="sr-only">Search</label>
                  <input
                    id="header-q"
                    name="q"
                    type="search"
                    maxLength={200}
                    placeholder="Search"
                    className="h-10 w-56 rounded-full border border-line bg-white px-4 text-sm"
                  />
                </form>
                <Link
                  href="/search"
                  aria-label="Search"
                  className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-sand md:hidden"
                >
                  <svg aria-hidden width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                    <circle cx="11" cy="11" r="7" />
                    <path d="m20 20-3.5-3.5" />
                  </svg>
                </Link>
                <Link
                  href="/account"
                  aria-label="Your account"
                  className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-sand"
                >
                  <svg aria-hidden width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                    <circle cx="12" cy="8" r="4" />
                    <path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
                  </svg>
                </Link>
                <CartButton />
              </div>
            </div>
            <nav aria-label="Categories" className="mx-auto max-w-6xl">
              <div className="no-scrollbar flex gap-1 overflow-x-auto px-2 pb-2">
                <Suspense fallback={<div className="h-9" />}>
                  <CategoryNav />
                </Suspense>
              </div>
            </nav>
          </header>
          <main className="flex-1">{children}</main>
          <footer className="mt-16 border-t border-line">
            <div className="mx-auto max-w-6xl px-4 py-8 text-sm text-muted">
              <nav aria-label="Help" className="mb-4 flex flex-wrap gap-x-5 gap-y-2">
                {POLICY_PAGES.map((p) => (
                  <Link key={p.slug} href={`/help/${p.slug}`} className="hover:text-ink">
                    {p.title}
                  </Link>
                ))}
              </nav>
              <p className="font-medium text-ink">{BRAND} is a fictional brand. This is a concept build.</p>
              <p className="mt-1">
                Product photos are AI-generated. Payments run in Paystack test mode, and nothing
                here is for sale.
              </p>
            </div>
          </footer>
          <CartDrawer />
          <Assistant />
        </CartProvider>
      </body>
    </html>
  );
}
