import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { getPolicy, POLICY_PAGES } from "@/lib/api";
import { Markdown } from "@/lib/markdown";

export function generateStaticParams() {
  return POLICY_PAGES.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata(props: PageProps<"/help/[slug]">): Promise<Metadata> {
  const { slug } = await props.params;
  const page = POLICY_PAGES.find((p) => p.slug === slug);
  return page ? { title: page.title } : {};
}

export default function HelpPage(props: PageProps<"/help/[slug]">) {
  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <nav aria-label="Help pages" className="mb-8 flex flex-wrap gap-2 text-sm">
        {POLICY_PAGES.map((p) => (
          <Link key={p.slug} href={`/help/${p.slug}`} className="rounded-full border border-line px-3 py-1.5 hover:bg-sand">
            {p.title}
          </Link>
        ))}
      </nav>
      <Suspense fallback={<div className="h-96 animate-pulse rounded bg-sand" />}>
        <Policy params={props.params} />
      </Suspense>
    </div>
  );
}

async function Policy({ params }: Pick<PageProps<"/help/[slug]">, "params">) {
  const markdown = await getPolicy((await params).slug);
  if (markdown === null) notFound();
  return (
    <article className="leading-relaxed">
      <Markdown text={markdown} />
    </article>
  );
}
