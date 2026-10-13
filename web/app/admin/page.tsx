import Image from "next/image";
import Link from "next/link";
import { StatusBadge } from "@/components/admin/StatusBadge";
import type { AdminProductRow } from "@/lib/admin-types";
import { categoryLabel } from "@/lib/catalog";
import { formatNaira } from "@/lib/format";
import { apiAs } from "@/lib/server-api";

const STATUSES = ["", "active", "draft", "archived"] as const;

export default async function AdminProducts(props: PageProps<"/admin">) {
  const sp = await props.searchParams;
  const q = typeof sp.q === "string" ? sp.q : "";
  const status = typeof sp.status === "string" && STATUSES.includes(sp.status as never) ? sp.status : "";
  const cursor = typeof sp.cursor === "string" ? sp.cursor : "";
  const query = new URLSearchParams({ limit: "40", ...(q && { q }), ...(status && { status }), ...(cursor && { cursor }) });
  const result = await apiAs<{ items: AdminProductRow[]; next_cursor: string | null }>(`/admin/products?${query}`);
  if (!result.ok) return <p className="text-clay">{result.error}</p>;
  const { items, next_cursor } = result.data;
  const nextHref = next_cursor
    ? `/admin?${new URLSearchParams({ ...(q && { q }), ...(status && { status }), cursor: next_cursor })}`
    : null;

  return (
    <>
      <h1 className="font-display text-2xl">Products</h1>
      <form className="mt-4 flex flex-wrap gap-2" action="/admin">
        <input
          name="q"
          defaultValue={q}
          placeholder="Search by name"
          aria-label="Search by name"
          className="h-10 min-w-0 flex-1 rounded-full border border-line bg-white px-4 text-sm"
        />
        <select name="status" defaultValue={status} aria-label="Status" className="h-10 rounded-full border border-line bg-cream px-3 text-sm">
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="draft">Draft</option>
          <option value="archived">Archived</option>
        </select>
        <button className="h-10 rounded-full bg-ink px-5 text-sm font-semibold text-cream">Search</button>
      </form>

      {items.length === 0 ? (
        <p className="py-10 text-muted">No products match.</p>
      ) : (
        <ul className="mt-5 divide-y divide-line rounded-lg border border-line bg-white" data-testid="admin-products">
          {items.map((p) => (
            <li key={p.id}>
              <Link href={`/admin/products/${p.id}`} className="flex items-center gap-3 p-3 hover:bg-cream">
                <div className="relative h-14 w-10 shrink-0 overflow-hidden rounded bg-sand">
                  {p.image && <Image src={p.image.url} alt="" fill sizes="40px" quality={70} className="object-cover" />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{p.name}</p>
                  <p className="text-xs text-muted">
                    {categoryLabel(p.category)} · {p.variant_count} variants · {p.available} available
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <span className="text-sm">{formatNaira(p.price_kobo)}</span>
                  <StatusBadge status={p.status} />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex justify-between text-sm">
        {cursor ? <Link href={`/admin?${new URLSearchParams({ ...(q && { q }), ...(status && { status }) })}`} className="underline underline-offset-4">First page</Link> : <span />}
        {nextHref && <Link href={nextHref} className="underline underline-offset-4">Next page</Link>}
      </div>
    </>
  );
}
