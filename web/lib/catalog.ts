import type { ListingQuery, Sort } from "./types";

// Display names and one-line blurbs for the category slugs the API returns.
export const CATEGORY_INFO: Record<string, { label: string; blurb: string }> = {
  ankara: { label: "Ankara", blurb: "Bold wax prints for every day and every party." },
  adire: { label: "Adire", blurb: "Hand-dyed indigo from Abeokuta traditions, cut for now." },
  "aso-oke": { label: "Aso-Oke", blurb: "Hand-woven ceremony cloth for owambe and weddings." },
  "agbada-kaftans": {
    label: "Agbada & Kaftans",
    blurb: "Flowing, embroidered and tailored for the big day.",
  },
  "ready-to-wear": { label: "Ready-to-Wear", blurb: "Lagos city staples in linen and cotton." },
  accessories: { label: "Accessories", blurb: "Beads, brass, leather and woven finishing touches." },
  headwear: { label: "Headwear", blurb: "Gele, fila and caps." },
};

export function categoryLabel(slug: string): string {
  return CATEGORY_INFO[slug]?.label ?? slug.replace(/-/g, " ");
}

export const SORTS: { value: Sort; label: string }[] = [
  { value: "newest", label: "Newest" },
  { value: "price_asc", label: "Price: low to high" },
  { value: "price_desc", label: "Price: high to low" },
  { value: "name", label: "Name" },
];

// Price filter buckets in kobo. "max" is inclusive.
export const PRICE_BANDS = [
  { value: "0-25000", label: "Under ₦25,000", min: 0, max: 2_499_900 },
  { value: "25000-50000", label: "₦25,000 – ₦50,000", min: 2_500_000, max: 5_000_000 },
  { value: "50000-100000", label: "₦50,000 – ₦100,000", min: 5_000_100, max: 10_000_000 },
  { value: "100000-", label: "Over ₦100,000", min: 10_000_100, max: undefined },
] as const;

type RawParams = Record<string, string | string[] | undefined>;

export type ListingFilters = {
  sort: Sort;
  size?: string;
  colour?: string;
  price?: string;
  in_stock: boolean;
};

const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) || undefined;

/** URL search params -> the filters shown in the UI. Unknown values are dropped. */
export function parseFilters(params: RawParams): ListingFilters {
  const sort = first(params.sort);
  const price = first(params.price);
  return {
    sort: SORTS.some((s) => s.value === sort) ? (sort as Sort) : "newest",
    size: first(params.size),
    colour: first(params.colour),
    price: PRICE_BANDS.some((b) => b.value === price) ? price : undefined,
    in_stock: first(params.in_stock) === "1",
  };
}

/** UI filters -> the API query (prices in kobo). */
export function toQuery(category: string, f: ListingFilters, limit = 24): ListingQuery {
  const band = PRICE_BANDS.find((b) => b.value === f.price);
  return {
    category,
    sort: f.sort,
    size: f.size,
    colour: f.colour,
    min_price_kobo: band?.min,
    max_price_kobo: band?.max,
    in_stock: f.in_stock || undefined,
    limit,
  };
}

export function queryString(q: ListingQuery): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(q)) {
    if (value !== undefined && value !== "") sp.set(key, String(value));
  }
  return sp.toString();
}
