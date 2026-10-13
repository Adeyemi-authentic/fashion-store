import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Filters } from "@/components/Filters";
import { LoadMore } from "@/components/LoadMore";
import { GridSkeleton, ProductCard, ProductGrid } from "@/components/ProductCard";
import { getCategories, getFacets, getProducts } from "@/lib/api";
import { CATEGORY_INFO, categoryLabel, parseFilters, queryString, toQuery } from "@/lib/catalog";

export async function generateStaticParams() {
  return (await getCategories()).map((c) => ({ category: c.slug }));
}

export async function generateMetadata(props: PageProps<"/c/[category]">): Promise<Metadata> {
  const { category } = await props.params;
  return { title: categoryLabel(category), description: CATEGORY_INFO[category]?.blurb };
}

export default function CategoryPage(props: PageProps<"/c/[category]">) {
  return (
    <div className="mx-auto max-w-6xl px-4 pt-6">
      <Suspense fallback={<div className="h-16" />}>
        <Heading params={props.params} />
      </Suspense>
      {/* Filters come from the URL, which is only known per request: stream them in. */}
      <Suspense fallback={<><div className="mt-5 h-10" /><div className="mt-6"><GridSkeleton /></div></>}>
        <Listing {...props} />
      </Suspense>
    </div>
  );
}

async function Heading({ params }: Pick<PageProps<"/c/[category]">, "params">) {
  const { category } = await params;
  const known = (await getCategories()).some((c) => c.slug === category);
  if (!known) notFound();
  return (
    <header>
      <h1 className="font-display text-3xl md:text-4xl">{categoryLabel(category)}</h1>
      {CATEGORY_INFO[category] && <p className="mt-1 text-muted">{CATEGORY_INFO[category].blurb}</p>}
    </header>
  );
}

async function Listing({ params, searchParams }: PageProps<"/c/[category]">) {
  const { category } = await params;
  const filters = parseFilters(await searchParams);
  const query = toQuery(category, filters);
  const [page, facets] = await Promise.all([getProducts(query), getFacets(category)]);
  const qs = queryString(query);

  return (
    <>
      <div className="mt-5">
        <Filters filters={filters} facets={facets} />
      </div>
      <div className="mt-6">
        <h2 className="sr-only">Products</h2>
        {page.items.length === 0 ? (
          <p className="py-16 text-center text-muted">Nothing matches these filters.</p>
        ) : (
          <ProductGrid>
            {page.items.map((p, i) => (
              <ProductCard key={p.id} product={p} eager={i < 2} />
            ))}
            <LoadMore key={qs} query={qs} initialCursor={page.next_cursor} />
          </ProductGrid>
        )}
      </div>
    </>
  );
}
