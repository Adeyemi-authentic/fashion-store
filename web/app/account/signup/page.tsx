import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthForm } from "@/components/account/AuthForm";
import { safeNext } from "@/lib/server-api";

export const metadata: Metadata = { title: "Create an account", robots: { index: false } };

export default function Page(props: PageProps<"/account/signup">) {
  return (
    <div className="mx-auto max-w-sm px-4 pt-10">
      <h1 className="font-display text-3xl">Create an account</h1>
      <p className="mt-1 text-sm text-muted">Anything in your bag comes with you.</p>
      <Suspense fallback={<div className="mt-6 h-72" />}>
        <Form searchParams={props.searchParams} />
      </Suspense>
    </div>
  );
}

async function Form({ searchParams }: Pick<PageProps<"/account/signup">, "searchParams">) {
  return <AuthForm mode="signup" next={safeNext((await searchParams).next)} />;
}
