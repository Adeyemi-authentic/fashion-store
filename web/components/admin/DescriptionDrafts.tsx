"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { approveDescriptionDraft, queueDescriptionDrafts, rejectDescriptionDraft } from "@/app/admin/actions";
import type { ActionResult, DescriptionDraft } from "@/lib/admin-types";

function useAction() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const run = (action: () => Promise<ActionResult>) =>
    startTransition(async () => {
      const r = await action();
      setResult(r);
      if (r.ok) router.refresh();
    });
  return { pending, result, run };
}

function Feedback({ result }: { result: ActionResult | null }) {
  if (!result) return null;
  return result.ok ? (
    <p className="text-sm text-emerald-800" role="status">{result.message}</p>
  ) : (
    <p className="text-sm text-clay" role="alert">{result.error}</p>
  );
}

export function DraftAllButton() {
  const { pending, result, run } = useAction();
  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        disabled={pending}
        onClick={() => run(() => queueDescriptionDrafts({ all_template: true }))}
        className="h-10 rounded-full bg-ink px-4 text-sm text-cream disabled:opacity-50"
      >
        {pending ? "Queuing…" : "Draft all template descriptions"}
      </button>
      <Feedback result={result} />
    </div>
  );
}

export function DraftOneButton({ productId }: { productId: string }) {
  const { pending, result, run } = useAction();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={pending}
        onClick={() => run(() => queueDescriptionDrafts({ product_ids: [productId] }))}
        className="h-9 rounded-full border border-line px-3 text-sm disabled:opacity-50"
      >
        {pending ? "Queuing…" : "Draft with AI"}
      </button>
      <Feedback result={result} />
    </div>
  );
}

export function DraftCard({ draft: d }: { draft: DescriptionDraft }) {
  const { pending, result, run } = useAction();
  const [editing, setEditing] = useState(d.status === "failed_checks");
  const [text, setText] = useState(d.text);
  const open = d.status === "needs_review" || d.status === "failed_checks";
  const words = text.trim().split(/\s+/).filter(Boolean).length;

  return (
    <li className="rounded-lg border border-line bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link href={`/admin/products/${d.product_id}`} className="font-medium underline-offset-4 hover:underline">
          {d.product_name}
        </Link>
        <span className="text-xs text-muted">
          {d.attempts} attempt{d.attempts === 1 ? "" : "s"} · {d.input_tokens + d.output_tokens} tokens · {d.model}
        </span>
      </div>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Live now</h3>
          <p className="mt-1 text-sm text-muted">{d.current_description || "(empty)"}</p>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Draft</h3>
          {editing && open ? (
            <>
              <label htmlFor={`draft-${d.id}`} className="sr-only">Edit draft</label>
              <textarea
                id={`draft-${d.id}`}
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={6}
                maxLength={2000}
                className="mt-1 w-full rounded-lg border border-line p-2 text-sm"
              />
              <p className="text-xs text-muted">{words} words (40–90)</p>
            </>
          ) : (
            <p className="mt-1 text-sm">{d.text}</p>
          )}
        </div>
      </div>
      <ul className="mt-3 flex flex-wrap gap-1.5 text-xs" aria-label="Checks">
        {d.checks.map((c) => (
          <li
            key={c.name}
            title={c.detail || undefined}
            className={`rounded-full px-2 py-1 ${c.passed ? "bg-emerald-50 text-emerald-900" : "bg-red-50 text-clay"}`}
          >
            {c.passed ? "✓" : "✗"} {c.name}
            {!c.passed && c.detail && <span>: {c.detail}</span>}
          </li>
        ))}
      </ul>
      {open && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={() => run(() => approveDescriptionDraft(d.id, d.product_slug, editing ? text : undefined))}
            className="h-9 rounded-full bg-ink px-4 text-sm text-cream disabled:opacity-50"
          >
            {editing ? "Save edit and approve" : "Approve"}
          </button>
          {!editing && (
            <button type="button" onClick={() => setEditing(true)} className="h-9 rounded-full border border-line px-4 text-sm">
              Edit
            </button>
          )}
          <button
            type="button"
            disabled={pending}
            onClick={() => run(() => rejectDescriptionDraft(d.id))}
            className="h-9 rounded-full px-3 text-sm text-clay underline underline-offset-2 disabled:opacity-50"
          >
            Reject
          </button>
          <Feedback result={result} />
        </div>
      )}
    </li>
  );
}
