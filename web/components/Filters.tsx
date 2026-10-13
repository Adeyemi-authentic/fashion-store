"use client";

import { usePathname, useRouter } from "next/navigation";
import { useOptimistic, useTransition } from "react";
import { type ListingFilters, PRICE_BANDS, SORTS } from "@/lib/catalog";
import type { Facets } from "@/lib/types";

/** Filters live in the URL, so a filtered listing can be shared, bookmarked and cached. */
export function Filters({ filters: current, facets }: { filters: ListingFilters; facets: Facets }) {
  const router = useRouter();
  const pathname = usePathname();
  const [pending, startTransition] = useTransition();
  // Show the new choice at once; the filtered page can take a moment to arrive.
  const [filters, setOptimistic] = useOptimistic(current);

  function update(key: keyof ListingFilters, value: string | boolean | undefined) {
    const next: Record<string, string> = {};
    const merged = { ...filters, [key]: value };
    if (merged.sort !== "newest") next.sort = merged.sort;
    if (merged.size) next.size = merged.size;
    if (merged.colour) next.colour = merged.colour;
    if (merged.price) next.price = merged.price;
    if (merged.in_stock) next.in_stock = "1";
    const qs = new URLSearchParams(next).toString();
    startTransition(() => {
      setOptimistic(merged);
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    });
  }

  const active = Boolean(filters.size || filters.colour || filters.price || filters.in_stock);
  const select =
    "h-10 rounded-full border border-line bg-cream pl-3 pr-8 text-sm text-ink focus:border-ink focus:outline-none";

  return (
    // One row that scrolls sideways on phones; wraps on wider screens.
    <div
      className="no-scrollbar -mx-4 flex items-center gap-2 overflow-x-auto px-4 md:mx-0 md:flex-wrap md:px-0 [&>*]:shrink-0"
      aria-busy={pending}
    >
      <label className="sr-only" htmlFor="f-sort">Sort by</label>
      <select id="f-sort" className={select} value={filters.sort} onChange={(e) => update("sort", e.target.value)}>
        {SORTS.map((s) => (
          <option key={s.value} value={s.value}>{s.label}</option>
        ))}
      </select>

      {facets.sizes.length > 1 && (
        <>
          <label className="sr-only" htmlFor="f-size">Size</label>
          <select id="f-size" className={select} value={filters.size ?? ""} onChange={(e) => update("size", e.target.value || undefined)}>
            <option value="">All sizes</option>
            {facets.sizes.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </>
      )}

      <label className="sr-only" htmlFor="f-colour">Colour</label>
      <select id="f-colour" className={select} value={filters.colour ?? ""} onChange={(e) => update("colour", e.target.value || undefined)}>
        <option value="">All colours</option>
        {facets.colours.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>

      <label className="sr-only" htmlFor="f-price">Price</label>
      <select id="f-price" className={select} value={filters.price ?? ""} onChange={(e) => update("price", e.target.value || undefined)}>
        <option value="">Any price</option>
        {PRICE_BANDS.map((b) => (
          <option key={b.value} value={b.value}>{b.label}</option>
        ))}
      </select>

      <label className="flex h-10 cursor-pointer items-center gap-2 rounded-full border border-line px-3 text-sm">
        <input
          type="checkbox"
          checked={filters.in_stock}
          onChange={(e) => update("in_stock", e.target.checked)}
          className="h-4 w-4 accent-ink"
        />
        In stock
      </label>

      {active && (
        <button
          type="button"
          onClick={() =>
            startTransition(() => {
              setOptimistic({ sort: filters.sort, in_stock: false });
              router.replace(filters.sort === "newest" ? pathname : `${pathname}?sort=${filters.sort}`, {
                scroll: false,
              });
            })
          }
          className="h-10 px-2 text-sm text-muted underline underline-offset-4"
        >
          Clear
        </button>
      )}
    </div>
  );
}
