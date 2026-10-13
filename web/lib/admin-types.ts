import type { Image } from "./types";

export type ProductStatus = "draft" | "active" | "archived";

export type AdminVariant = {
  id: string;
  sku: string;
  size: string;
  colour: string;
  price_kobo: number;
  stock_on_hand: number;
  stock_reserved: number;
  available: number;
};

export type AdminProduct = {
  id: string;
  slug: string;
  name: string;
  description: string;
  category: string;
  status: ProductStatus;
  price_kobo: number;
  attributes: Record<string, unknown>;
  image: Image | null;
  variants: AdminVariant[];
};

export type AdminProductRow = {
  id: string;
  slug: string;
  name: string;
  category: string;
  status: ProductStatus;
  price_kobo: number;
  variant_count: number;
  available: number;
  image: Image | null;
};

export type AdminOrder = {
  id: string;
  status: string;
  total_kobo: number;
  currency: string;
  email: string | null;
  is_guest: boolean;
  item_count: number;
  needs_review: boolean;
  created_at: string;
};

export type NewVariant = { size: string; colour: string; price_kobo: number; stock_on_hand: number };

export type ProductInput = {
  name: string;
  category: string;
  description: string;
  status: "draft" | "active";
  image_url?: string;
  variants: NewVariant[];
};

export type ProductPatch = Partial<{
  name: string;
  category: string;
  description: string;
  status: ProductStatus;
  image_url: string;
}>;

export type ActionResult = { ok: true; message?: string } | { ok: false; error: string };
