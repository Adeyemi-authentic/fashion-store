import type { Metadata } from "next";
import { Suspense } from "react";
import { SearchResults } from "@/components/search/SearchResults";
import { GridSkeleton } from "@/components/ProductCard";

export const metadata: Metadata = { title: "Search", robots: { index: false } };

// The page shell is prerendered; results are fetched in the browser through /api/search,
// so the API sees each shopper's own IP for its per-IP rate limit (a server-side fetch
// would make every shopper look like this server).
export default function SearchPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 pt-6">
      <Suspense fallback={<GridSkeleton />}>
        <SearchResults />
      </Suspense>
    </div>
  );
}
