import Link from "next/link";
import { CancelOrderButton } from "@/components/admin/CancelOrderButton";
import { StatusBadge } from "@/components/admin/StatusBadge";
import type { AdminOrder } from "@/lib/admin-types";
import { formatNaira } from "@/lib/format";
import { apiAs } from "@/lib/server-api";

const STATUSES = ["pending_payment", "paid", "fulfilled", "cancelled", "expired", "refund_pending", "refunded"];
const dateFmt = new Intl.DateTimeFormat("en-NG", { dateStyle: "medium", timeStyle: "short", timeZone: "Africa/Lagos" });

export default async function AdminOrders(props: PageProps<"/admin/orders">) {
  const sp = await props.searchParams;
  const status = typeof sp.status === "string" && STATUSES.includes(sp.status) ? sp.status : "";
  const cursor = typeof sp.cursor === "string" ? sp.cursor : "";
  const query = new URLSearchParams({ ...(status && { status }), ...(cursor && { cursor }) });
  const result = await apiAs<{ items: AdminOrder[]; next_cursor: string | null }>(`/admin/orders?${query}`);
  if (!result.ok) return <p className="text-clay">{result.error}</p>;
  const { items, next_cursor } = result.data;

  return (
    <>
      <h1 className="font-display text-2xl">Orders</h1>
      <div className="no-scrollbar -mx-4 mt-4 flex gap-2 overflow-x-auto px-4 text-sm">
        {["", ...STATUSES].map((s) => (
          <Link
            key={s || "all"}
            href={s ? `/admin/orders?status=${s}` : "/admin/orders"}
            aria-current={s === status ? "page" : undefined}
            className={`shrink-0 rounded-full border px-3 py-1.5 ${s === status ? "border-ink bg-ink text-cream" : "border-line"}`}
          >
            {s ? s.replace("_", " ") : "All"}
          </Link>
        ))}
      </div>
      {items.length === 0 ? (
        <p className="py-10 text-muted">No orders{status && ` with status “${status.replace("_", " ")}”`} yet.</p>
      ) : (
        <ul className="mt-5 divide-y divide-line rounded-lg border border-line bg-white">
          {items.map((o) => (
            <li key={o.id} className="flex items-center gap-3 p-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{o.email ?? "unknown"}{o.is_guest && <span className="text-muted"> (guest)</span>}</p>
                <p className="text-xs text-muted">{dateFmt.format(new Date(o.created_at))} · {o.item_count} items · #{o.id.slice(0, 8)}</p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <span>{formatNaira(o.total_kobo)}</span>
                <span className="flex gap-1">
                  {o.needs_review && <StatusBadge status="needs review" />}
                  <StatusBadge status={o.status} />
                </span>
                {o.status === "pending_payment" && <CancelOrderButton orderId={o.id} />}
              </div>
            </li>
          ))}
        </ul>
      )}
      {next_cursor && (
        <Link href={`/admin/orders?${new URLSearchParams({ ...(status && { status }), cursor: next_cursor })}`} className="mt-4 inline-block text-sm underline underline-offset-4">
          Next page
        </Link>
      )}
    </>
  );
}
