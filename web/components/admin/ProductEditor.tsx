"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { addVariant, adjustStock, archiveProduct, setPrice, updateProduct } from "@/app/admin/actions";
import type { ActionResult, AdminProduct, AdminVariant } from "@/lib/admin-types";
import { CATEGORY_INFO } from "@/lib/catalog";
import { formatNaira } from "@/lib/format";
import { inputClass, koboToNaira, nairaToKobo } from "./fields";
import { StatusBadge } from "./StatusBadge";

/** Runs a Server Action, shows its message, and re-reads the page's data on success. */
function useAction() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const run = (action: () => Promise<ActionResult>) =>
    startTransition(async () => {
      const r = await action();
      setResult(r);
      if (r.ok) router.refresh();
    });
  return { pending, result, run, setResult };
}

function Feedback({ result }: { result: ActionResult | null }) {
  if (!result) return null;
  return result.ok ? (
    <p className="text-sm text-emerald-800" role="status">{result.message ?? "Saved"}</p>
  ) : (
    <p className="text-sm text-clay" role="alert">{result.error}</p>
  );
}

export function ProductEditor({ product }: { product: AdminProduct }) {
  const details = useAction();

  function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const image = String(f.get("image_url") ?? "");
    details.run(() =>
      updateProduct(product.id, {
        name: String(f.get("name")),
        category: String(f.get("category")),
        description: String(f.get("description")),
        status: f.get("status") as AdminProduct["status"],
        ...(image && image !== product.image?.url ? { image_url: image } : {}),
      }),
    );
  }

  return (
    <div className="space-y-10">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-display text-2xl">{product.name}</h1>
        <StatusBadge status={product.status} />
        {product.status === "active" && (
          <Link href={`/p/${product.slug}`} className="text-sm underline underline-offset-4">View in shop</Link>
        )}
      </div>

      <form onSubmit={save} className="max-w-2xl space-y-4" key={product.id + product.status}>
        <label className="block text-sm">
          Name
          <input name="name" defaultValue={product.name} required minLength={2} maxLength={120} className={inputClass} />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">
            Category
            <select name="category" defaultValue={product.category} className={inputClass}>
              {Object.entries(CATEGORY_INFO).map(([slug, c]) => (
                <option key={slug} value={slug}>{c.label}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Status
            <select name="status" defaultValue={product.status} className={inputClass}>
              <option value="draft">Draft (hidden)</option>
              <option value="active">Active (in the shop)</option>
              <option value="archived">Archived</option>
            </select>
          </label>
        </div>
        <label className="block text-sm">
          Description
          <textarea name="description" defaultValue={product.description} rows={4} maxLength={5000} className={`${inputClass} h-auto py-2`} />
        </label>
        <label className="block text-sm">
          Image path
          <input name="image_url" defaultValue={product.image?.url ?? ""} pattern="/images/.*" className={inputClass} />
        </label>
        <p className="text-xs text-muted">Slug: {product.slug} (never changes, so links and caches stay valid)</p>
        <div className="flex items-center gap-4">
          <button disabled={details.pending} className="h-10 rounded-full bg-ink px-5 text-sm font-semibold text-cream disabled:opacity-50">
            {details.pending ? "Saving…" : "Save details"}
          </button>
          <Feedback result={details.result} />
        </div>
      </form>

      <section>
        <h2 className="font-semibold">Variants, prices and stock</h2>
        <p className="mt-1 text-xs text-muted">
          Stock changes are added or removed (+5 for a delivery, −1 for a damaged item), never overwritten, so a sale
          happening at the same moment isn&apos;t lost. On hand can&apos;t go below what&apos;s held for unpaid orders.
        </p>
        <ul className="mt-3 space-y-3">
          {product.variants.map((v) => (
            <VariantRow key={v.id} variant={v} slug={product.slug} />
          ))}
        </ul>
        <AddVariant productId={product.id} />
      </section>

      {product.status !== "archived" && <ArchiveButton productId={product.id} />}
    </div>
  );
}

function VariantRow({ variant: v, slug }: { variant: AdminVariant; slug: string }) {
  const price = useAction();
  const stock = useAction();
  const [priceText, setPriceText] = useState(koboToNaira(v.price_kobo));
  const [delta, setDelta] = useState("");
  const [reason, setReason] = useState("");

  return (
    <li className="rounded-lg border border-line bg-white p-3 text-sm" data-testid="variant-row">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">{v.colour} · {v.size} <span className="text-xs font-normal text-muted">{v.sku}</span></p>
        <p className="text-xs text-muted" data-testid="variant-stock">
          On hand <b className="text-ink">{v.stock_on_hand}</b> · held <b className="text-ink">{v.stock_reserved}</b> · available{" "}
          <b className="text-ink">{v.available}</b>
        </p>
      </div>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const kobo = nairaToKobo(priceText);
            if (kobo === null) return price.setResult({ ok: false, error: "Enter a price in naira" });
            price.run(() => setPrice(v.id, kobo));
          }}
        >
          <label className="flex-1">
            <span className="text-xs text-muted">Price (₦) · now {formatNaira(v.price_kobo)}</span>
            <input value={priceText} onChange={(e) => setPriceText(e.target.value)} inputMode="decimal" aria-label={`Price for ${v.sku}`} className={inputClass} />
          </label>
          <button disabled={price.pending} className="h-10 rounded-full border border-ink px-4 disabled:opacity-50">Save</button>
        </form>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number(delta);
            if (!Number.isInteger(n) || n === 0) return stock.setResult({ ok: false, error: "Enter a whole number like 5 or -2" });
            stock.run(async () => {
              const r = await adjustStock(v.id, slug, n, reason);
              if (r.ok) {
                setDelta("");
                setReason("");
              }
              return r;
            });
          }}
        >
          <label className="w-20">
            <span className="text-xs text-muted">± units</span>
            <input value={delta} onChange={(e) => setDelta(e.target.value)} inputMode="numeric" placeholder="+5" aria-label={`Stock change for ${v.sku}`} className={inputClass} />
          </label>
          <label className="flex-1">
            <span className="text-xs text-muted">Reason</span>
            <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} placeholder="delivery" aria-label={`Reason for ${v.sku}`} className={inputClass} />
          </label>
          <button disabled={stock.pending} className="h-10 rounded-full border border-ink px-4 disabled:opacity-50">Apply</button>
        </form>
      </div>
      <div className="mt-1 flex gap-4">
        <Feedback result={price.result} />
        <Feedback result={stock.result} />
      </div>
    </li>
  );
}

function AddVariant({ productId }: { productId: string }) {
  const action = useAction();
  return (
    <form
      className="mt-4 grid grid-cols-2 gap-2 rounded-lg border border-dashed border-line p-3 sm:grid-cols-[1fr_1.5fr_1fr_1fr_auto]"
      onSubmit={(e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const f = new FormData(form);
        const price = nairaToKobo(String(f.get("price")));
        const stock = Number(f.get("stock") || 0);
        if (price === null || !Number.isInteger(stock) || stock < 0) {
          return action.setResult({ ok: false, error: "Enter a price in naira and a whole-number stock" });
        }
        action.run(async () => {
          const r = await addVariant(productId, {
            size: String(f.get("size")),
            colour: String(f.get("colour")),
            price_kobo: price,
            stock_on_hand: stock,
          });
          if (r.ok) form.reset();
          return r;
        });
      }}
    >
      <input name="size" required placeholder="Size" aria-label="New variant size" className={inputClass} />
      <input name="colour" required placeholder="Colour" aria-label="New variant colour" className={inputClass} />
      <input name="price" required placeholder="Price ₦" inputMode="decimal" aria-label="New variant price in naira" className={inputClass} />
      <input name="stock" placeholder="Stock" inputMode="numeric" aria-label="New variant stock" className={inputClass} />
      <button disabled={action.pending} className="mt-1 h-10 rounded-full bg-ink px-4 text-sm font-semibold text-cream disabled:opacity-50">Add</button>
      <div className="col-span-full"><Feedback result={action.result} /></div>
    </form>
  );
}

function ArchiveButton({ productId }: { productId: string }) {
  const action = useAction();
  const [confirming, setConfirming] = useState(false);
  return (
    <section className="border-t border-line pt-6">
      <h2 className="font-semibold">Archive</h2>
      <p className="mt-1 text-sm text-muted">Hides it from the shop. Past orders keep pointing at it, so it&apos;s never deleted.</p>
      {confirming ? (
        <div className="mt-3 flex gap-2">
          <button onClick={() => action.run(() => archiveProduct(productId))} disabled={action.pending} className="h-10 rounded-full bg-clay px-5 text-sm font-semibold text-white">
            Yes, archive it
          </button>
          <button onClick={() => setConfirming(false)} className="h-10 px-3 text-sm underline">Cancel</button>
        </div>
      ) : (
        <button onClick={() => setConfirming(true)} className="mt-3 h-10 rounded-full border border-clay px-5 text-sm text-clay">
          Archive product
        </button>
      )}
      <Feedback result={action.result} />
    </section>
  );
}
