"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { cancelOrder } from "@/app/admin/actions";

export function CancelOrderButton({ orderId }: { orderId: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <button type="button" onClick={() => setConfirming(true)} className="text-xs text-clay underline underline-offset-2">
        Cancel
      </button>
    );
  }
  return (
    <span className="flex items-center gap-2 text-xs">
      <button
        type="button"
        disabled={pending}
        onClick={() =>
          startTransition(async () => {
            const r = await cancelOrder(orderId);
            if (r.ok) router.refresh();
            else setError(r.error);
          })
        }
        className="rounded-full bg-clay px-2 py-1 text-white disabled:opacity-50"
      >
        {pending ? "…" : "Yes, cancel"}
      </button>
      {error && <span className="text-clay" role="alert">{error}</span>}
    </span>
  );
}
