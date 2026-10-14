import Link from "next/link";
import { DraftCard, DraftAllButton } from "@/components/admin/DescriptionDrafts";
import type { DescriptionDraft, DraftStatus } from "@/lib/admin-types";
import { apiAs } from "@/lib/server-api";

const TABS: { status: DraftStatus; label: string }[] = [
  { status: "needs_review", label: "Ready to review" },
  { status: "failed_checks", label: "Failed checks" },
  { status: "approved", label: "Approved" },
  { status: "rejected", label: "Rejected" },
];

export default async function AdminDescriptions(props: PageProps<"/admin/descriptions">) {
  const sp = await props.searchParams;
  const status = TABS.find((t) => t.status === sp.status)?.status ?? "needs_review";
  const result = await apiAs<DescriptionDraft[]>(`/admin/description-drafts?status=${status}`);
  if (!result.ok) return <p className="text-clay">{result.error}</p>;

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl">Descriptions</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Claude drafts each description from the product&apos;s facts. Automatic checks then
            look for anything the facts don&apos;t say: rule checks for materials, colours,
            claims, prices and tone, then Claude itself for paraphrased claims. Nothing goes
            live until you approve it.
          </p>
        </div>
        <DraftAllButton />
      </div>
      <div className="no-scrollbar -mx-4 mt-4 flex gap-2 overflow-x-auto px-4 text-sm">
        {TABS.map((t) => (
          <Link
            key={t.status}
            href={`/admin/descriptions?status=${t.status}`}
            aria-current={t.status === status ? "page" : undefined}
            className={`shrink-0 rounded-full border px-3 py-1.5 ${t.status === status ? "border-ink bg-ink text-cream" : "border-line"}`}
          >
            {t.label}
          </Link>
        ))}
      </div>
      {result.data.length === 0 ? (
        <p className="py-10 text-muted">No drafts here.</p>
      ) : (
        <ul className="mt-5 space-y-4">
          {result.data.map((d) => (
            <DraftCard key={d.id} draft={d} />
          ))}
        </ul>
      )}
    </>
  );
}
