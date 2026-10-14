// Shapes returned by the FastAPI app. Money is always integer kobo (ADR-004).

export type Image = { url: string; alt: string };

export type ProductCard = {
  id: string;
  slug: string;
  name: string;
  category: string;
  price_kobo: number;
  in_stock: boolean;
  image: Image | null;
};

export type ProductPage = { items: ProductCard[]; next_cursor: string | null };

export type Variant = {
  id: string;
  sku: string;
  size: string;
  colour: string;
  price_kobo: number;
  available: number;
};

export type ProductDetail = {
  id: string;
  slug: string;
  name: string;
  description: string;
  category: string;
  price_kobo: number;
  attributes: Record<string, string | string[]>;
  in_stock: boolean;
  variants: Variant[];
  images: Image[];
};

export type Category = { slug: string; product_count: number };

export type Facets = {
  sizes: string[];
  colours: string[];
  min_price_kobo: number | null;
  max_price_kobo: number | null;
};

export type CartLine = {
  variant_id: string;
  sku: string;
  product_slug: string;
  product_name: string;
  size: string;
  colour: string;
  image: Image | null;
  unit_price_kobo: number;
  qty: number;
  line_total_kobo: number;
  available: number;
  problem: "unavailable" | "sold_out" | "not_enough_stock" | null;
};

export type Cart = {
  items: CartLine[];
  item_count: number;
  subtotal_kobo: number;
  can_checkout: boolean;
};

export type Sort = "newest" | "price_asc" | "price_desc" | "name";

export type ListingQuery = {
  category?: string;
  sort?: Sort;
  size?: string;
  colour?: string;
  min_price_kobo?: number;
  max_price_kobo?: number;
  in_stock?: boolean;
  limit?: number;
  cursor?: string;
};

// GET /search: what the search understood from the query (shown to the shopper), and the
// results. Filters are empty when the LLM was skipped (short query) or unavailable.
export type SearchFilters = {
  category: string | null;
  min_price_kobo: number | null;
  max_price_kobo: number | null;
  gender: string | null;
  occasion: string | null;
  colours: string[];
};

export type SearchResponse = {
  query: string;
  items: ProductCard[];
  filters: SearchFilters;
  relaxed: boolean;
};

export type Recommendations = { similar: ProductCard[]; bought_together: ProductCard[] };

export type AssistantMessage = { role: "user" | "assistant"; text: string; products: ProductCard[] };
