import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { VariantPicker } from "@/components/VariantPicker";
import { getAllSlugs, getProduct } from "@/lib/api";
import { categoryLabel } from "@/lib/catalog";

// All ~130 product pages are built ahead of time; a product added later is rendered on its
// first visit and then cached (ISR).
export async function generateStaticParams() {
  return (await getAllSlugs()).map((slug) => ({ slug }));
}

export async function generateMetadata(props: PageProps<"/p/[slug]">): Promise<Metadata> {
  const product = await getProduct((await props.params).slug);
  if (!product) return {};
  return {
    title: product.name,
    description: product.description.slice(0, 160),
    openGraph: { images: product.images.slice(0, 1).map((i) => i.url) },
  };
}

export default function ProductPage(props: PageProps<"/p/[slug]">) {
  return (
    <Suspense fallback={<ProductSkeleton />}>
      <Product params={props.params} />
    </Suspense>
  );
}

const ATTRIBUTE_LABELS: Record<string, string> = {
  fabric: "Fabric",
  pattern: "Pattern",
  fit: "Fit",
  occasion: "Occasion",
  gender: "Cut",
};

async function Product({ params }: Pick<PageProps<"/p/[slug]">, "params">) {
  const product = await getProduct((await params).slug);
  if (!product) notFound();
  const image = product.images[0];
  const details = Object.entries(ATTRIBUTE_LABELS)
    .filter(([key]) => product.attributes[key])
    .map(([key, label]) => {
      const value = product.attributes[key];
      return [label, Array.isArray(value) ? value.join(", ") : value] as const;
    });

  return (
    <div className="mx-auto max-w-6xl md:grid md:grid-cols-2 md:gap-10 md:px-4 md:pt-8">
      <div className="relative aspect-[2/3] bg-sand md:overflow-hidden md:rounded-lg">
        {image && (
          <Image
            src={image.url}
            alt={image.alt}
            fill
            sizes="(min-width: 768px) 50vw, 100vw"
            quality={70}
            loading="eager"
            fetchPriority="high"
            className="object-cover"
          />
        )}
      </div>
      <div className="px-4 pt-5 md:px-0 md:pt-0">
        <nav aria-label="Breadcrumb" className="text-sm text-muted">
          <Link href={`/c/${product.category}`} className="underline-offset-4 hover:underline">
            {categoryLabel(product.category)}
          </Link>
        </nav>
        <h1 className="mt-1 font-display text-3xl leading-tight md:text-4xl">{product.name}</h1>
        <div className="mt-3">
          <VariantPicker product={product} />
        </div>
        <section className="mt-8 border-t border-line pt-6">
          <h2 className="font-semibold">Details</h2>
          <p className="mt-2 leading-relaxed text-muted">{product.description}</p>
          {details.length > 0 && (
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
              {details.map(([label, value]) => (
                <div key={label} className="contents">
                  <dt className="text-muted">{label}</dt>
                  <dd className="first-letter:uppercase">{value}</dd>
                </div>
              ))}
            </dl>
          )}
          <p className="mt-4 text-xs text-muted">Photo is AI-generated. Fictional brand, concept build.</p>
        </section>
      </div>
    </div>
  );
}

function ProductSkeleton() {
  return (
    <div className="mx-auto max-w-6xl md:grid md:grid-cols-2 md:gap-10 md:px-4 md:pt-8" aria-hidden>
      <div className="aspect-[2/3] animate-pulse bg-sand md:rounded-lg" />
      <div className="space-y-3 px-4 pt-5 md:px-0 md:pt-0">
        <div className="h-4 w-24 rounded bg-sand" />
        <div className="h-9 w-3/4 rounded bg-sand" />
        <div className="h-6 w-28 rounded bg-sand" />
      </div>
    </div>
  );
}
