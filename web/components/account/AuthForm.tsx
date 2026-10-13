"use client";

import Link from "next/link";
import { useState } from "react";

type Mode = "login" | "signup";

export function AuthForm({ mode, next }: { mode: Mode; next: string }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      // Through the /api proxy, so the session cookie is set on this site (ADR-005).
      const res = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      if (res.ok) {
        // A full page load so the header, cart and every cached view pick up the new session.
        window.location.assign(next);
        return;
      }
      const body = await res.json().catch(() => null);
      if (res.status === 422) {
        setError(mode === "signup" ? "Use a valid email and a password of at least 8 characters." : "Enter a valid email and password.");
      } else {
        setError(typeof body?.detail === "string" ? body.detail : "Something went wrong. Please try again.");
      }
    } catch {
      setError("Can't reach the shop right now. Check your connection.");
    }
    setBusy(false);
  }

  const input = "mt-1 h-12 w-full rounded-lg border border-line bg-white px-3 text-base focus:border-ink focus:outline-none";
  const other = mode === "login" ? "signup" : "login";
  const nextQuery = next !== "/account" ? `?next=${encodeURIComponent(next)}` : "";

  return (
    <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
      <label className="block text-sm">
        Email
        <input name="email" type="email" autoComplete="email" required className={input} />
      </label>
      <label className="block text-sm">
        Password
        <input
          name="password"
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          minLength={mode === "signup" ? 8 : undefined}
          required
          className={input}
        />
        {mode === "signup" && <span className="mt-1 block text-xs text-muted">At least 8 characters.</span>}
      </label>
      {error && (
        <p className="text-sm text-clay" role="alert">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="h-12 w-full rounded-full bg-ink text-sm font-semibold text-cream disabled:opacity-50"
      >
        {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
      </button>
      <p className="text-center text-sm text-muted">
        {mode === "login" ? "New here? " : "Already have an account? "}
        <Link href={`/account/${other}${nextQuery}`} className="text-ink underline underline-offset-4">
          {mode === "login" ? "Create an account" : "Sign in"}
        </Link>
      </p>
    </form>
  );
}
