"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Cart } from "@/lib/types";

type Result = { ok: true } | { ok: false; message: string };

type CartContextValue = {
  cart: Cart | null; // null until the first load finishes
  isOpen: boolean;
  open: () => void;
  close: () => void;
  add: (variantId: string, qty?: number) => Promise<Result>;
  setQty: (variantId: string, qty: number) => Promise<Result>;
};

const CartContext = createContext<CartContextValue | null>(null);

export function useCart(): CartContextValue {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used inside <CartProvider>");
  return ctx;
}

async function call(path: string, init?: RequestInit): Promise<Cart | { error: string }> {
  try {
    const res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    });
    const body = await res.json().catch(() => null);
    if (res.ok) return body as Cart;
    const detail = body?.detail;
    if (res.status === 409 && detail?.message) return { error: detail.message };
    if (res.status === 404) return { error: "This item is no longer available" };
    return { error: "Something went wrong. Please try again." };
  } catch {
    return { error: "Can't reach the shop right now. Check your connection." };
  }
}

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [cart, setCart] = useState<Cart | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  // Changes run one after another. The very first add creates the cart and its cookie; if
  // a second add raced it, it would arrive without the cookie and make a second cart.
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const run = useCallback((path: string, init: RequestInit): Promise<Result> => {
    const next = queue.current.then(async (): Promise<Result> => {
      const result = await call(path, init);
      if ("error" in result) return { ok: false, message: result.error };
      setCart(result);
      return { ok: true };
    });
    queue.current = next;
    return next;
  }, []);

  useEffect(() => {
    call("/cart").then((result) => {
      if (!("error" in result)) setCart(result);
    });
  }, []);

  const add = useCallback(
    (variantId: string, qty = 1) =>
      run("/cart/items", { method: "POST", body: JSON.stringify({ variant_id: variantId, qty }) }),
    [run],
  );

  const setQty = useCallback(
    (variantId: string, qty: number) =>
      run(`/cart/items/${variantId}`, { method: "PATCH", body: JSON.stringify({ qty }) }),
    [run],
  );

  const value: CartContextValue = {
    cart,
    isOpen,
    open: useCallback(() => setIsOpen(true), []),
    close: useCallback(() => setIsOpen(false), []),
    add,
    setQty,
  };
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}
