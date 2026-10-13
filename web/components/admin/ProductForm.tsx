"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { createProduct } from "@/app/admin/actions";
import type { NewVariant } from "@/lib/admin-types";
import { CATEGORY_INFO } from "@/lib/catalog";
import { inputClass, nairaToKobo } from "./fields";

type Row = { size: string; colour: string; price: string; stock: string };
const blank: Row = { size: "", colour: "", price: "", stock: "0" };

export function NewProductForm() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([{ ...blank }]);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const variants: NewVariant[] = [];
    for (const [i, r] of rows.entries()) {
      const price = nairaToKobo(r.price);
      const stock = Number(r.stock);
      if (!r.size.trim() || !r.colour.trim() || price === null || !Number.isInteger(stock) || stock < 0) {
        setError(`Variant ${i + 1}: fill in size, colour, a price in naira and a whole-number stock.`);
        return;
      }
      variants.push({ size: r.size, colour: r.colour, price_kobo: price, stock_on_hand: stock });
    }
    setError(null);
    startTransition(async () => {
      const result = await createProduct({
        name: String(f.get("name")),
        category: String(f.get("category")),
        description: String(f.get("description")),
        status: f.get("publish") ? "active" : "draft",
        image_url: String(f.get("image_url")) || undefined,
        variants,
      });
      if (result.ok && result.id) router.push(`/admin/products/${result.id}`);
      else if (!result.ok) setError(result.error);
    });
  }

  const set = (i: number, key: keyof Row, value: string) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)));

  return (
    <form onSubmit={submit} className="mt-5 max-w-2xl space-y-4">
      <label className="block text-sm">
        Name
        <input name="name" required minLength={2} maxLength={120} className={inputClass} />
      </label>
      <label className="block text-sm">
        Category
        <select name="category" className={inputClass} defaultValue="ankara">
          {Object.entries(CATEGORY_INFO).map(([slug, c]) => (
            <option key={slug} value={slug}>{c.label}</option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        Description
        <textarea name="description" rows={3} maxLength={5000} className={`${inputClass} h-auto py-2`} />
      </label>
      <label className="block text-sm">
        Image path <span className="text-muted">(e.g. /images/products/ankara-akoka-wrap-dress.webp)</span>
        <input name="image_url" pattern="/images/.*" className={inputClass} />
      </label>

      <fieldset>
        <legend className="text-sm font-semibold">Variants</legend>
        <div className="mt-2 space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-line p-2 sm:grid-cols-[1fr_1.5fr_1fr_1fr_auto]">
              <input aria-label={`Variant ${i + 1} size`} placeholder="Size" value={r.size} onChange={(e) => set(i, "size", e.target.value)} className={inputClass} />
              <input aria-label={`Variant ${i + 1} colour`} placeholder="Colour" value={r.colour} onChange={(e) => set(i, "colour", e.target.value)} className={inputClass} />
              <input aria-label={`Variant ${i + 1} price in naira`} placeholder="Price ₦" inputMode="decimal" value={r.price} onChange={(e) => set(i, "price", e.target.value)} className={inputClass} />
              <input aria-label={`Variant ${i + 1} stock`} placeholder="Stock" inputMode="numeric" value={r.stock} onChange={(e) => set(i, "stock", e.target.value)} className={inputClass} />
              <button
                type="button"
                onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                disabled={rows.length === 1}
                className="mt-1 h-10 px-2 text-sm text-muted underline disabled:opacity-30"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
        <button type="button" onClick={() => setRows((rs) => [...rs, { ...blank }])} className="mt-2 text-sm underline underline-offset-4">
          + Add variant
        </button>
      </fieldset>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="publish" className="h-4 w-4 accent-ink" /> Publish now (needs an image)
      </label>
      {error && <p className="text-sm text-clay" role="alert">{error}</p>}
      <button disabled={pending} className="h-11 rounded-full bg-ink px-6 text-sm font-semibold text-cream disabled:opacity-50">
        {pending ? "Saving…" : "Create product"}
      </button>
    </form>
  );
}
