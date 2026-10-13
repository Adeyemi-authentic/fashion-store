"use client";

import { useState } from "react";
import type { ProductCard as Card, ProductPage } from "@/lib/types";
import { ProductCard } from "./ProductCard";

/** Appends further pages using the API's keyset cursor. The first page is server-rendered. */
export function LoadMore({ query, initialCursor }: { query: string; initialCursor: string | null }) {
  const [items, setItems] = useState<Card[]>([]);
  const [cursor, setCursor] = useState(initialCursor);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");

  async function load() {
    if (!cursor) return;
    setState("loading");
    try {
      const res = await fetch(`/api/products?${query}&cursor=${encodeURIComponent(cursor)}`);
      if (!res.ok) throw new Error(String(res.status));
      const page = (await res.json()) as ProductPage;
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
      setState("idle");
    } catch {
      setState("error");
    }
  }

  return (
    <>
      {items.map((p) => (
        <ProductCard key={p.id} product={p} />
      ))}
      {cursor && (
        <li className="col-span-full mt-4 flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={load}
            disabled={state === "loading"}
            className="rounded-full border border-ink px-6 py-3 text-sm font-semibold disabled:opacity-50"
          >
            {state === "loading" ? "Loading…" : "Show more"}
          </button>
          {state === "error" && (
            <p className="text-sm text-clay" role="alert">
              Couldn&apos;t load more. Try again.
            </p>
          )}
        </li>
      )}
    </>
  );
}
