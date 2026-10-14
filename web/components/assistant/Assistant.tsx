"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useCart } from "@/components/cart/CartProvider";
import { BRAND } from "@/lib/brand";
import { formatNaira } from "@/lib/format";
import { Markdown } from "@/lib/markdown";
import type { AssistantMessage, ProductCard } from "@/lib/types";

const STARTERS = [
  "Something for an owambe under ₦50,000",
  "What's your returns policy?",
  "Which size am I if my bust is 92 cm?",
  "Where is my order?",
];

type Shown = AssistantMessage & { error?: boolean };

// Not a modal: on a laptop the shopper can keep browsing while the chat stays open. On a
// phone the panel fills the screen and closes when a link in it is followed.
export function Assistant() {
  const { refresh, open: openCart } = useCart();
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<Shown[] | null>(null); // null until loaded
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);

  // The conversation lives on the server (cookie), so it survives page changes and reloads.
  useEffect(() => {
    if (!isOpen || messages !== null) return;
    fetch("/api/assistant/messages", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { messages: [] }))
      .then((b: { messages: AssistantMessage[] }) => setMessages(b.messages))
      .catch(() => setMessages([]));
  }, [isOpen, messages]);

  useEffect(() => {
    if (isOpen) input.current?.focus();
  }, [isOpen]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages, busy]);

  const close = useCallback(() => {
    setIsOpen(false);
    launcher.current?.focus();
  }, []);

  const closeOnPhone = useCallback(() => {
    if (window.matchMedia("(max-width: 639px)").matches) setIsOpen(false);
  }, []);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy || messages === null) return;
    setDraft("");
    setMessages((m) => [...(m ?? []), { role: "user", text: message, products: [] }]);
    setBusy(true);
    let reply: Shown;
    try {
      const res = await fetch("/api/assistant/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const body = await res.json().catch(() => null);
      if (res.ok) {
        reply = body.reply;
        if (body.cart_changed) await refresh();
      } else {
        const detail = typeof body?.detail === "string" ? body.detail : null;
        reply = {
          role: "assistant",
          products: [],
          error: true,
          text:
            res.status === 429
              ? "You're sending messages quickly. Please wait a minute and try again."
              : (detail ?? "Sorry, something went wrong. Please try again."),
        };
      }
    } catch {
      reply = { role: "assistant", products: [], error: true, text: "Can't reach the shop right now. Check your connection." };
    }
    setMessages((m) => [...(m ?? []), reply]);
    setBusy(false);
    input.current?.focus();
  }

  async function startOver() {
    await fetch("/api/assistant/messages", { method: "DELETE" }).catch(() => null);
    setMessages([]);
    input.current?.focus();
  }

  return (
    <>
      {!isOpen && (
        <button
          ref={launcher}
          type="button"
          onClick={() => setIsOpen(true)}
          className="fixed bottom-4 right-4 z-30 flex h-12 items-center gap-2 rounded-full bg-ink px-4 text-sm font-medium text-cream shadow-lg hover:bg-indigo"
        >
          <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M4 5h16v11H9l-5 4V5Z" />
          </svg>
          Ask {BRAND}
        </button>
      )}

      {isOpen && (
        <section
          role="dialog"
          aria-modal="false"
          aria-labelledby="assistant-title"
          onKeyDown={(e) => e.key === "Escape" && close()}
          className="fixed inset-0 z-30 flex flex-col bg-cream shadow-2xl sm:inset-auto sm:bottom-4 sm:right-4 sm:h-[min(640px,calc(100dvh-2rem))] sm:w-[400px] sm:rounded-2xl sm:border sm:border-line"
        >
          <header className="flex items-center justify-between border-b border-line px-4 py-3">
            <div>
              <h2 id="assistant-title" className="font-display text-lg leading-tight">Shopping assistant</h2>
              <p className="text-xs text-muted">AI answers. Prices and stock come from the shop.</p>
            </div>
            <div className="flex">
              <button
                type="button"
                onClick={startOver}
                className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-sand"
                aria-label="Start a new chat"
                title="New chat"
              >
                <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4" />
                </svg>
              </button>
              <button
                type="button"
                onClick={close}
                className="-mr-2 flex h-11 w-11 items-center justify-center rounded-full hover:bg-sand"
                aria-label="Close the assistant"
              >
                <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </div>
          </header>

          <div className="flex-1 overflow-y-auto px-4 py-4" aria-live="polite" aria-busy={busy}>
            {messages === null ? (
              <p className="text-sm text-muted">Loading…</p>
            ) : messages.length === 0 ? (
              <div>
                <p className="text-sm">
                  Hi! I can find outfits, check sizes and stock, add things to your bag, and answer
                  questions about delivery, returns and your orders.
                </p>
                <ul className="mt-4 flex flex-col items-start gap-2">
                  {STARTERS.map((s) => (
                    <li key={s}>
                      <button
                        type="button"
                        onClick={() => send(s)}
                        className="rounded-full border border-line px-3 py-2 text-left text-sm hover:bg-sand"
                      >
                        {s}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <ol className="flex flex-col gap-3">
                {messages.map((m, i) => (
                  <li key={i} className={m.role === "user" ? "flex justify-end" : ""}>
                    {m.role === "user" ? (
                      <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-ink px-3.5 py-2 text-sm text-cream">
                        {m.text}
                      </p>
                    ) : (
                      <div className={`max-w-[95%] text-sm leading-relaxed ${m.error ? "text-clay" : ""}`}>
                        <span className="sr-only">Assistant: </span>
                        <Markdown text={m.text} compact onLinkClick={closeOnPhone} />
                        {m.products.length > 0 && <Cards products={m.products} onClick={closeOnPhone} />}
                      </div>
                    )}
                  </li>
                ))}
                {busy && (
                  <li className="text-sm text-muted">
                    <span className="inline-flex gap-1" aria-label="The assistant is typing">
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:120ms]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:240ms]" />
                    </span>
                  </li>
                )}
              </ol>
            )}
            <div ref={end} />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
            className="flex items-end gap-2 border-t border-line p-3"
          >
            <label htmlFor="assistant-input" className="sr-only">Message</label>
            <textarea
              ref={input}
              id="assistant-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(draft);
                }
              }}
              rows={1}
              maxLength={1000}
              placeholder="Ask about outfits, sizes, delivery…"
              className="max-h-32 min-h-11 flex-1 resize-none rounded-xl border border-line bg-white px-3 py-2.5 text-base sm:text-sm"
            />
            <button
              type="submit"
              disabled={busy || messages === null || !draft.trim()}
              className="h-11 rounded-xl bg-ink px-4 text-sm font-medium text-cream disabled:opacity-40"
            >
              Send
            </button>
          </form>
          <p className="px-3 pb-2 text-center text-[11px] text-muted">
            The assistant can add items to your bag but can&apos;t check out for you.{" "}
            <button type="button" onClick={openCart} className="underline">Open bag</button>
          </p>
        </section>
      )}
    </>
  );
}

function Cards({ products, onClick }: { products: ProductCard[]; onClick: () => void }) {
  return (
    <ul className="no-scrollbar mt-3 flex gap-2 overflow-x-auto pb-1">
      {products.map((p) => (
        <li key={p.id} className="w-28 shrink-0">
          <Link href={`/p/${p.slug}`} onClick={onClick} className="block">
            <div className="relative aspect-[2/3] overflow-hidden rounded-md bg-sand">
              {p.image && <Image src={p.image.url} alt={p.image.alt} fill sizes="112px" quality={70} className="object-cover" />}
              {!p.in_stock && (
                <span className="absolute left-1 top-1 rounded-full bg-cream/90 px-1.5 py-0.5 text-[10px] font-medium">Sold out</span>
              )}
            </div>
            <p className="mt-1 line-clamp-2 text-xs leading-snug">{p.name}</p>
            <p className="text-xs font-semibold">From {formatNaira(p.price_kobo)}</p>
          </Link>
        </li>
      ))}
    </ul>
  );
}
