"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { GridSkeleton, ProductCard, ProductGrid } from "@/components/ProductCard";
import { categoryLabel } from "@/lib/catalog";
import { formatNaira } from "@/lib/format";
import type { SearchFilters, SearchResponse } from "@/lib/types";

// The answer for one query. While the URL's query differs from `q` here, a search is in
// flight (derived, so the effect never has to set a "loading" state itself).
type Answer = { q: string; data: SearchResponse } | { q: string; error: string };

/** What the search understood, in words a shopper reads: "Under ₦50,000", "For weddings". */
function describe(f: SearchFilters): string[] {
  const out: string[] = [];
  if (f.category) out.push(categoryLabel(f.category));
  if (f.gender) out.push(f.gender === "women" ? "Women" : "Men");
  if (f.occasion) out.push(`For ${f.occasion}`);
  if (f.colours.length) out.push(f.colours.join(" or "));
  if (f.min_price_kobo !== null && f.max_price_kobo !== null) {
    out.push(`${formatNaira(f.min_price_kobo)} – ${formatNaira(f.max_price_kobo)}`);
  } else if (f.max_price_kobo !== null) out.push(`Under ${formatNaira(f.max_price_kobo)}`);
  else if (f.min_price_kobo !== null) out.push(`Over ${formatNaira(f.min_price_kobo)}`);
  return out;
}

export function SearchResults() {
  const q = (useSearchParams().get("q") ?? "").trim();
  const router = useRouter();
  const [answer, setAnswer] = useState<Answer | null>(null);
  const loading = q !== "" && answer?.q !== q;

  useEffect(() => {
    if (!q) return;
    const controller = new AbortController();
    fetch(`/api/search?${new URLSearchParams({ q })}`, { signal: controller.signal })
      .then(async (res) => {
        if (res.status === 429) throw new Error("Too many searches. Please wait a minute and try again.");
        if (!res.ok) throw new Error("Search isn't working right now. Please try again.");
        setAnswer({ q, data: (await res.json()) as SearchResponse });
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") setAnswer({ q, error: err.message });
      });
    return () => controller.abort();
  }, [q]);

  return (
    <>
      <form
        role="search"
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const value = new FormData(e.currentTarget).get("q")?.toString().trim();
          if (value) router.push(`/search?${new URLSearchParams({ q: value })}`);
        }}
      >
        <label htmlFor="search-page-q" className="sr-only">Search</label>
        <input
          id="search-page-q"
          name="q"
          type="search"
          defaultValue={q}
          key={q}
          autoFocus={!q}
          maxLength={200}
          placeholder="Try “wedding outfit under 50k”"
          className="h-12 min-w-0 flex-1 rounded-full border border-line bg-white px-5"
        />
        <button className="h-12 rounded-full bg-ink px-5 text-cream">Search</button>
      </form>

      {loading && (
        <div className="mt-6" aria-live="polite">
          <p className="sr-only">Searching…</p>
          <GridSkeleton />
        </div>
      )}
      {!loading && answer && "error" in answer && (
        <p className="mt-8 text-clay" role="alert">{answer.error}</p>
      )}
      {!loading && answer && "data" in answer && <Results data={answer.data} />}
    </>
  );
}

function Results({ data }: { data: SearchResponse }) {
  const understood = describe(data.filters);
  return (
    <div className="mt-5" aria-live="polite">
      <h1 className="font-display text-2xl">
        {data.items.length ? `Results for “${data.query}”` : `Nothing found for “${data.query}”`}
      </h1>
      {understood.length > 0 && (
        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-sm text-muted">
          Showing
          {understood.map((u) => (
            <span key={u} className="rounded-full bg-sand px-2.5 py-1 text-ink">{u}</span>
          ))}
        </p>
      )}
      {data.relaxed && (
        <p className="mt-2 text-sm text-muted">
          Nothing matched every part of your search, so these are the closest matches.
        </p>
      )}
      {data.items.length > 0 && (
        <div className="mt-6">
          <ProductGrid>
            {data.items.map((p, i) => (
              <ProductCard key={p.id} product={p} eager={i < 2} />
            ))}
          </ProductGrid>
        </div>
      )}
    </div>
  );
}
