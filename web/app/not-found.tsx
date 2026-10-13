import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-4 py-24 text-center">
      <h1 className="font-display text-3xl">We couldn&apos;t find that</h1>
      <p className="mt-2 text-muted">It may have sold out for good or moved.</p>
      <Link href="/" className="mt-6 inline-flex rounded-full bg-ink px-6 py-3 text-sm font-semibold text-cream">
        Back to the shop
      </Link>
    </div>
  );
}
