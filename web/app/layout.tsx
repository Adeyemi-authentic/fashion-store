import type { Metadata, Viewport } from "next";
import { Fraunces, Inter } from "next/font/google";
import Link from "next/link";
import { Suspense } from "react";
import { CartButton } from "@/components/cart/CartButton";
import { CartDrawer } from "@/components/cart/CartDrawer";
import { CartProvider } from "@/components/cart/CartProvider";
import { getCategories } from "@/lib/api";
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
              <CartButton />
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
              <p className="font-medium text-ink">{BRAND} is a fictional brand. This is a concept build.</p>
              <p className="mt-1">
                Product photos are AI-generated. Payments run in Paystack test mode, and nothing
                here is for sale.
              </p>
            </div>
          </footer>
          <CartDrawer />
        </CartProvider>
      </body>
    </html>
  );
}
