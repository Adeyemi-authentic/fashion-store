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

export async function cancelOrder(orderId: string): Promise<ActionResult> {
  const r = await apiAs(`/admin/orders/${orderId}/cancel`, { method: "POST" });
  if (!r.ok) return { ok: false, error: r.error };
  shopChanged(); // its held stock is free again
  return { ok: true, message: "Cancelled" };
}

export async function queueDescriptionDrafts(
  input: { product_ids?: string[]; all_template?: boolean },
): Promise<ActionResult> {
  const r = await apiAs<{ queued: number }>("/admin/description-drafts/queue", { method: "POST", body: input });
  if (!r.ok) return { ok: false, error: r.error };
  const n = r.data.queued;
  return {
    ok: true,
    message: n === 0 ? "Nothing to draft" : `Drafting ${n} description${n === 1 ? "" : "s"}: they appear under Descriptions as they finish`,
  };
}

export async function approveDescriptionDraft(draftId: string, slug: string, text?: string): Promise<ActionResult> {
  const r = await apiAs(`/admin/description-drafts/${draftId}/approve`, { method: "POST", body: text === undefined ? {} : { text } });
  if (!r.ok) return { ok: false, error: r.error };
  // `slug` only picks which page to refresh; the API already applied the change.
  shopChanged(slug);
  return { ok: true, message: "Approved: live on the shop" };
}

export async function rejectDescriptionDraft(draftId: string): Promise<ActionResult> {
  const r = await apiAs(`/admin/description-drafts/${draftId}/reject`, { method: "POST" });
  if (!r.ok) return { ok: false, error: r.error };
  return { ok: true, message: "Rejected" };
}
