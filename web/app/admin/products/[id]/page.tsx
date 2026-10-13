import { notFound } from "next/navigation";
import { ProductEditor } from "@/components/admin/ProductEditor";
import type { AdminProduct } from "@/lib/admin-types";
import { apiAs } from "@/lib/server-api";

export default async function EditProductPage(props: PageProps<"/admin/products/[id]">) {
  const { id } = await props.params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const result = await apiAs<AdminProduct>(`/admin/products/${id}`);
  if (!result.ok) {
    if (result.status === 404) notFound();
    return <p className="text-clay">{result.error}</p>;
  }
  return <ProductEditor product={result.data} />;
}
