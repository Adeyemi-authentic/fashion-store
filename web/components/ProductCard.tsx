import Image from "next/image";
import Link from "next/link";
import { formatNaira } from "@/lib/format";
import type { ProductCard as Card } from "@/lib/types";

// Grid columns: 2 on phones, 3 on tablets, 4 on desktop.
const SIZES = "(min-width: 1024px) 25vw, (min-width: 768px) 33vw, 50vw";

export function ProductCard({ product, eager = false }: { product: Card; eager?: boolean }) {
  return (
    <li>
      <Link href={`/p/${product.slug}`} className="group block" data-testid="product-card">
        <div className="relative aspect-[2/3] overflow-hidden rounded-lg bg-sand">
          {product.image && (
            <Image
              src={product.image.url}
              alt={product.image.alt}
              fill
              sizes={SIZES}
              quality={70}
              loading={eager ? "eager" : "lazy"}
              fetchPriority={eager ? "high" : "auto"}
              className="object-cover transition-transform duration-500 group-hover:scale-[1.03]"
            />
          )}
          {!product.in_stock && (
            <span className="absolute left-2 top-2 rounded-full bg-cream/90 px-2.5 py-1 text-xs font-medium">
              Sold out
            </span>
          )}
        </div>
        <h3 className="mt-2.5 text-sm leading-snug">{product.name}</h3>
        <p className="mt-0.5 text-sm font-semibold">From {formatNaira(product.price_kobo)}</p>
      </Link>
    </li>
  );
}

export function ProductGrid({ children }: { children: React.ReactNode }) {
  return (
    <ul className="grid grid-cols-2 gap-x-3 gap-y-7 md:grid-cols-3 md:gap-x-5 lg:grid-cols-4">
      {children}
    </ul>
  );
}

export function GridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <ul className="grid grid-cols-2 gap-x-3 gap-y-7 md:grid-cols-3 md:gap-x-5 lg:grid-cols-4" aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <li key={i}>
          <div className="aspect-[2/3] animate-pulse rounded-lg bg-sand" />
          <div className="mt-2.5 h-4 w-3/4 rounded bg-sand" />
          <div className="mt-1.5 h-4 w-1/3 rounded bg-sand" />
        </li>
      ))}
    </ul>
  );
}
