import { NewProductForm } from "@/components/admin/ProductForm";

export default function NewProductPage() {
  return (
    <>
      <h1 className="font-display text-2xl">New product</h1>
      <p className="mt-1 text-sm text-muted">Saved as a draft unless you publish it. The web address is made from the category and name.</p>
      <NewProductForm />
    </>
  );
}
