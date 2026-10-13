"use server";

import { updateTag } from "next/cache";
import type { ActionResult, AdminProduct, NewVariant, ProductInput, ProductPatch } from "@/lib/admin-types";
import { apiAs } from "@/lib/server-api";

// Every action forwards the caller's own cookie to the API, and the API decides (403 for
// non-admins). Server Actions are public POST endpoints, so this file deliberately trusts
// nothing about the caller. The storefront cache is only refreshed after the API has
// accepted the change.

function shopChanged(slug?: string) {
  // Listings, category pages and the product's own page. updateTag (not revalidateTag) so
  // the admin's next view of the shop waits for fresh data instead of showing the old page.
  updateTag("products");
  if (slug) updateTag(`product:${slug}`);
}

export async function createProduct(input: ProductInput): Promise<ActionResult & { id?: string }> {
  const r = await apiAs<{ id: string; slug: string }>("/admin/products", { method: "POST", body: input });
  if (!r.ok) return { ok: false, error: r.error };
  shopChanged(r.data.slug);
  return { ok: true, id: r.data.id };
}

export async function updateProduct(id: string, patch: ProductPatch): Promise<ActionResult> {
  const r = await apiAs<AdminProduct>(`/admin/products/${id}`, { method: "PATCH", body: patch });
  if (!r.ok) return { ok: false, error: r.error };
  shopChanged(r.data.slug);
  return { ok: true, message: "Saved" };
}

export async function archiveProduct(id: string): Promise<ActionResult> {
  const r = await apiAs<AdminProduct>(`/admin/products/${id}`, { method: "DELETE" });
  if (!r.ok) return { ok: false, error: r.error };
  shopChanged(r.data.slug);
  return { ok: true, message: "Archived" };
}

export async function addVariant(productId: string, variant: NewVariant): Promise<ActionResult> {
  const r = await apiAs<AdminProduct>(`/admin/products/${productId}/variants`, { method: "POST", body: variant });
  if (!r.ok) return { ok: false, error: r.error };
  shopChanged(r.data.slug);
  return { ok: true, message: "Variant added" };
}

export async function setPrice(variantId: string, priceKobo: number): Promise<ActionResult> {
  const r = await apiAs<AdminProduct>(`/admin/variants/${variantId}`, { method: "PATCH", body: { price_kobo: priceKobo } });
  if (!r.ok) return { ok: false, error: r.error };
  shopChanged(r.data.slug);
  return { ok: true, message: "Price saved" };
}

export async function adjustStock(variantId: string, slug: string, delta: number, reason: string): Promise<ActionResult> {
  const r = await apiAs(`/admin/variants/${variantId}/stock`, { method: "POST", body: { delta, reason } });
  if (!r.ok) return { ok: false, error: r.error };
  // `slug` only picks which page to refresh; the API already checked the change is allowed.
  shopChanged(slug);
  return { ok: true, message: "Stock updated" };
}
