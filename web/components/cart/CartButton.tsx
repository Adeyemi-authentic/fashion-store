"use client";

import { useCart } from "./CartProvider";

export function CartButton() {
  const { cart, open } = useCart();
  const count = cart?.item_count ?? 0;
  return (
    <button
      type="button"
      onClick={open}
      className="relative -mr-2 flex h-11 w-11 items-center justify-center rounded-full hover:bg-sand"
      aria-label={`Open bag, ${count} ${count === 1 ? "item" : "items"}`}
    >
      <svg aria-hidden width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M5 8h14l-1.2 12.2a1 1 0 0 1-1 .8H7.2a1 1 0 0 1-1-.8L5 8Z" />
        <path d="M9 8V6a3 3 0 0 1 6 0v2" />
      </svg>
      {count > 0 && (
        <span className="absolute right-1 top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-clay px-1 text-[11px] font-semibold text-white">
          {count}
        </span>
      )}
    </button>
  );
}
