import "server-only";
import { cacheLife, cacheTag } from "next/cache";
import { queryString } from "./catalog";
import type { Category, Facets, ListingQuery, ProductDetail, ProductPage, Recommendations } from "./types";

// Server components call the API directly (server to server). Browsers use /api/* instead.
const API = process.env.API_BASE_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API}${path}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return (await res.json()) as T;
}

export async function getProducts(q: ListingQuery): Promise<ProductPage> {
  "use cache";
  cacheLife("catalog");
  cacheTag("products");
  return (await get<ProductPage>(`/products?${queryString(q)}`))!;
}

export async function getProduct(slug: string): Promise<ProductDetail | null> {
  "use cache";
  cacheLife("catalog");
  cacheTag("products", `product:${slug}`);
  return get<ProductDetail>(`/products/${encodeURIComponent(slug)}`);
}

export async function getCategories(): Promise<Category[]> {
  "use cache";
  cacheLife("catalog");
  cacheTag("products");
  return (await get<Category[]>("/categories"))!;
}

export async function getFacets(category: string): Promise<Facets> {
  "use cache";
  cacheLife("catalog");
  cacheTag("products");
  return (await get<Facets>(`/facets?category=${encodeURIComponent(category)}`))!;
}

/** Every active product slug, for prerendering product pages at build time. */
export async function getAllSlugs(): Promise<string[]> {
  "use cache";
  cacheLife("catalog");
  const slugs: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await getProducts({ sort: "name", limit: 60, cursor });
    slugs.push(...page.items.map((p) => p.slug));
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  return slugs;
}

/** "Similar items" and "often bought together" for a product page. Cached like the rest of
 * the catalog; the API drops sold-out items when it builds the lists. */
export async function getRecommendations(slug: string): Promise<Recommendations> {
  "use cache";
  cacheLife("catalog");
  cacheTag("products", `product:${slug}`);
  return (await get<Recommendations>(`/products/${encodeURIComponent(slug)}/recommendations`)) ?? {
    similar: [],
    bought_together: [],
  };
}
