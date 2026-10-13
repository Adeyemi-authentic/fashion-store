"use client";

import { useEffect, useMemo, useState } from "react";
import { formatNaira } from "@/lib/format";
import type { ProductDetail, Variant } from "@/lib/types";
import { useHydrated } from "@/lib/useHydrated";
import { useCart } from "./cart/CartProvider";

const LOW_STOCK = 3;
const SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"];
const sizeRank = (size: string) => (SIZE_ORDER.includes(size) ? SIZE_ORDER.indexOf(size) : SIZE_ORDER.length);

/**
 * The page around this is cached for a few minutes, so the stock it was rendered with can be
 * out of date. On mount this asks the API for live counts; adding to the bag re-checks again
 * on the server, so a stale number can never put more in the bag than exists.
 */
export function VariantPicker({ product }: { product: ProductDetail }) {
  const { add, open } = useCart();
  const hydrated = useHydrated();
  const [variants, setVariants] = useState<Variant[]>(product.variants);
  // attributes.colours lists the pictured colour first (the seed photographs that one), so the
  // picker opens on the colour the photo shows.
  const listed = product.attributes.colours;
  const pictured = Array.isArray(listed) ? listed[0] : undefined;
  const colours = useMemo(() => {
    const order = Array.isArray(listed) ? listed : [];
    const rank = (c: string) => (order.includes(c) ? order.indexOf(c) : order.length);
    return [...new Set(variants.map((v) => v.colour))].sort((a, b) => rank(a) - rank(b));
  }, [variants, listed]);
  const [colour, setColour] = useState(colours[0]);
  const [size, setSize] = useState<string | null>(null);
  const [status, setStatus] = useState<{ kind: "idle" | "adding" | "added" | "error"; message?: string }>({
    kind: "idle",
  });

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/products/${encodeURIComponent(product.slug)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((fresh: ProductDetail | null) => {
        if (fresh && !cancelled) setVariants(fresh.variants);
      })
      .catch(() => {}); // keep the cached numbers; the cart re-checks anyway
    return () => {
      cancelled = true;
    };
  }, [product.slug]);

  const forColour = variants
    .filter((v) => v.colour === colour)
    .sort((a, b) => sizeRank(a.size) - sizeRank(b.size));
  const oneSize = forColour.length === 1 && forColour[0].size === "One Size";
  const selected = oneSize ? forColour[0] : forColour.find((v) => v.size === size);
  const price = selected?.price_kobo ?? Math.min(...forColour.map((v) => v.price_kobo));

  async function addToBag() {
    if (!selected) {
      setStatus({ kind: "error", message: "Choose a size first." });
      return;
    }
    setStatus({ kind: "adding" });
    const result = await add(selected.id);
    if (result.ok) {
      setStatus({ kind: "added" });
      open();
    } else {
      setStatus({ kind: "error", message: result.message });
    }
  }

  return (
    <div>
      <p className="text-xl font-semibold" data-testid="price">
        {selected || forColour.every((v) => v.price_kobo === price) ? "" : "From "}
        {formatNaira(price)}
      </p>

      <fieldset className="mt-6">
        <legend className="text-sm">
          Colour: <span className="font-semibold">{colour}</span>
          {pictured && colour !== pictured && <span className="text-muted"> (pictured in {pictured})</span>}
        </legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {colours.map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={c === colour}
              disabled={!hydrated}
              onClick={() => {
                setColour(c);
                setStatus({ kind: "idle" });
              }}
              className={`min-h-11 rounded-full border px-4 text-sm ${c === colour ? "border-ink bg-ink text-cream" : "border-line hover:border-ink"}`}
            >
              {c}
            </button>
          ))}
        </div>
      </fieldset>

      {!oneSize && (
        <fieldset className="mt-5">
          <legend className="text-sm">Size</legend>
          <div className="mt-2 grid grid-cols-4 gap-2 sm:grid-cols-6">
            {forColour.map((v) => {
              const soldOut = v.available === 0;
              return (
                <button
                  key={v.id}
                  type="button"
                  aria-pressed={v.size === size}
                  disabled={soldOut || !hydrated}
                  onClick={() => {
                    setSize(v.size);
                    setStatus({ kind: "idle" });
                  }}
                  aria-label={soldOut ? `${v.size}, sold out` : v.size}
                  className={`h-11 rounded-lg border text-sm ${
                    v.size === size ? "border-ink bg-ink text-cream" : "border-line hover:border-ink"
                  } ${soldOut ? "cursor-not-allowed text-muted line-through opacity-60" : ""}`}
                >
                  {v.size}
                </button>
              );
            })}
          </div>
        </fieldset>
      )}

      <p className="mt-4 min-h-5 text-sm" data-testid="stock" aria-live="polite">
        {selected &&
          (selected.available === 0 ? (
            <span className="text-clay">Sold out in this {oneSize ? "colour" : "size"}</span>
          ) : selected.available <= LOW_STOCK ? (
            <span className="text-clay">Only {selected.available} left</span>
          ) : (
            <span className="text-muted">In stock</span>
          ))}
        {!selected && forColour.every((v) => v.available === 0) && (
          <span className="text-clay">Sold out in this colour</span>
        )}
      </p>

      <button
        type="button"
        onClick={addToBag}
        disabled={!hydrated || status.kind === "adding" || selected?.available === 0}
        className="mt-3 w-full rounded-full bg-ink py-4 text-sm font-semibold text-cream disabled:opacity-50"
      >
        {status.kind === "adding" ? "Adding…" : "Add to bag"}
      </button>
      {status.kind === "error" && (
        <p className="mt-2 text-sm text-clay" role="alert">
          {status.message}
        </p>
      )}
    </div>
  );
}
