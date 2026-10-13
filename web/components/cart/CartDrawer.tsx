"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { formatNaira } from "@/lib/format";
import type { CartLine } from "@/lib/types";
import { useCart } from "./CartProvider";

const PROBLEM_TEXT: Record<NonNullable<CartLine["problem"]>, (l: CartLine) => string> = {
  unavailable: () => "No longer available. Please remove it.",
  sold_out: () => "Sold out. Please remove it.",
  not_enough_stock: (l) => `Only ${l.available} left. Lower the quantity to continue.`,
};

export function CartDrawer() {
  const { cart, isOpen, close } = useCart();
  const dialog = useRef<HTMLDialogElement>(null);

  // A native <dialog> opened with showModal() traps focus, closes on Esc and makes the page
  // behind it inert, so the drawer is accessible without a library.
  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (isOpen && !d.open) d.showModal();
    if (!isOpen && d.open) d.close();
  }, [isOpen]);

  return (
    <dialog
      ref={dialog}
      onClose={close}
      onClick={(e) => e.target === dialog.current && close()} // click on the backdrop
      aria-labelledby="cart-title"
      className="cart-drawer m-0 ml-auto h-dvh max-h-dvh w-full max-w-md bg-cream p-0 text-ink"
    >
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h2 id="cart-title" className="font-display text-xl">
            Your bag {cart && cart.item_count > 0 && <span className="text-muted">({cart.item_count})</span>}
          </h2>
          <button
            type="button"
            onClick={close}
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-full hover:bg-sand"
            aria-label="Close bag"
          >
            <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>

        {!cart || cart.items.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
            <p className="text-muted">{cart ? "Your bag is empty." : "Loading your bag…"}</p>
            {cart && (
              <Link href="/" onClick={close} className="rounded-full border border-ink px-5 py-2.5 text-sm font-medium">
                Start shopping
              </Link>
            )}
          </div>
        ) : (
          <>
            <ul className="flex-1 divide-y divide-line overflow-y-auto px-5">
              {cart.items.map((line) => (
                <CartLineRow key={line.variant_id} line={line} />
              ))}
            </ul>
            <div className="border-t border-line px-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-4">
              <div className="flex items-baseline justify-between">
                <span>Subtotal</span>
                <span className="text-lg font-semibold" data-testid="cart-subtotal">
                  {formatNaira(cart.subtotal_kobo)}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted">Delivery is worked out at checkout.</p>
              {cart.can_checkout ? (
                <Link
                  href="/checkout"
                  onClick={close}
                  className="mt-4 block w-full rounded-full bg-ink py-3.5 text-center text-sm font-semibold text-cream"
                >
                  Checkout
                </Link>
              ) : (
                <p className="mt-4 rounded-lg bg-sand p-3 text-sm">Fix the items marked above to check out.</p>
              )}
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}

function CartLineRow({ line }: { line: CartLine }) {
  const { setQty, close } = useCart();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(qty: number) {
    setBusy(true);
    setError(null);
    const result = await setQty(line.variant_id, qty);
    if (!result.ok) setError(result.message);
    setBusy(false);
  }

  const canIncrease = line.problem === null && line.qty < Math.min(line.available, 10);

  return (
    <li className="flex gap-4 py-4" data-testid="cart-line">
      <Link href={`/p/${line.product_slug}`} onClick={close} className="shrink-0">
        {line.image ? (
          <Image
            src={line.image.url}
            alt={line.image.alt}
            width={72}
            height={108}
            quality={70}
            className="h-[108px] w-[72px] rounded-md bg-sand object-cover"
          />
        ) : (
          <div className="h-[108px] w-[72px] rounded-md bg-sand" />
        )}
      </Link>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex justify-between gap-2">
          <Link href={`/p/${line.product_slug}`} onClick={close} className="font-medium leading-snug">
            {line.product_name}
          </Link>
          <span className="shrink-0 text-sm font-medium">{formatNaira(line.line_total_kobo)}</span>
        </div>
        <p className="mt-0.5 text-sm text-muted">
          {line.colour} · {line.size}
        </p>
        {line.problem && <p className="mt-1 text-sm text-clay">{PROBLEM_TEXT[line.problem](line)}</p>}
        {error && (
          <p className="mt-1 text-sm text-clay" role="alert">
            {error}
          </p>
        )}
        <div className="mt-auto flex items-center justify-between pt-2">
          <div className="flex items-center rounded-full border border-line" aria-busy={busy}>
            <button
              type="button"
              className="h-9 w-9 rounded-full text-lg disabled:opacity-30"
              onClick={() => change(line.qty - 1)}
              disabled={busy || line.qty <= 1}
              aria-label={`Decrease quantity of ${line.product_name}`}
            >
              −
            </button>
            <span className="w-6 text-center text-sm tabular-nums" aria-live="polite" data-testid="cart-line-qty">
              {line.qty}
            </span>
            <button
              type="button"
              className="h-9 w-9 rounded-full text-lg disabled:opacity-30"
              onClick={() => change(line.qty + 1)}
              disabled={busy || !canIncrease}
              aria-label={`Increase quantity of ${line.product_name}`}
            >
              +
            </button>
          </div>
          <button
            type="button"
            onClick={() => change(0)}
            disabled={busy}
            className="py-2 text-sm text-muted underline underline-offset-4"
          >
            Remove
          </button>
        </div>
      </div>
    </li>
  );
}
