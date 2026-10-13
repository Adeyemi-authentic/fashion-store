import "server-only";
import { cookies } from "next/headers";

// Per-user API calls made on the server (account and admin pages, Server Actions). They pass
// the browser's cookies on, so the API sees the same session and enforces its own checks.
// Never cached: the answer depends on who is asking.
const API = process.env.API_BASE_URL ?? "http://localhost:8000";

export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; error: string };

export async function apiAs<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<ApiResult<T>> {
  const res = await fetch(`${API}${path}`, {
    method: init.method ?? "GET",
    headers: {
      cookie: (await cookies()).toString(),
      ...(init.body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  const body = await res.json().catch(() => null);
  if (res.ok) return { ok: true, data: body as T };
  return { ok: false, status: res.status, error: errorMessage(body, res.status) };
}

function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
      .join("; ");
  }
  return `Request failed (${status})`;
}

export type Me = { id: string; email: string; role: "customer" | "admin" };

export async function getMe(): Promise<Me | null> {
  const result = await apiAs<Me>("/auth/me");
  return result.ok ? result.data : null;
}

/** Only same-site relative paths, so ?next= can't send someone to another website. */
export function safeNext(next: string | string[] | undefined, fallback = "/account"): string {
  const value = Array.isArray(next) ? next[0] : next;
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") ? value : fallback;
}
