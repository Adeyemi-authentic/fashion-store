const STYLES: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-900",
  draft: "bg-sand text-ink",
  archived: "bg-stone-200 text-stone-600",
  paid: "bg-emerald-100 text-emerald-900",
  fulfilled: "bg-indigo-100 text-indigo-900",
  pending_payment: "bg-amber-100 text-amber-900",
  expired: "bg-stone-200 text-stone-600",
  cancelled: "bg-stone-200 text-stone-600",
  refund_pending: "bg-red-100 text-red-900",
  refunded: "bg-stone-200 text-stone-600",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STYLES[status] ?? "bg-sand"}`}>
      {status.replace("_", " ")}
    </span>
  );
}
