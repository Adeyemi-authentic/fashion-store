import { type NextRequest, NextResponse } from "next/server";

// Runs before every /api/* request is forwarded to the FastAPI app (ADR-005):
// - X-Request-ID: one id from the browser's request through the API's logs and its jobs
// - X-Forwarded-For: the shopper's IP (on Vercel this header is set by the platform, so a
//   client can't fake it), for per-IP rate limits
// - X-Proxy-Auth: proves to the API that the request came through us, so it may believe
//   X-Forwarded-For. Only ever sent upstream, never to the browser.
export function proxy(request: NextRequest) {
  const headers = new Headers(request.headers);
  const incoming = headers.get("x-request-id");
  if (!incoming || !/^[A-Za-z0-9._-]{8,64}$/.test(incoming)) {
    headers.set("x-request-id", crypto.randomUUID().replaceAll("-", ""));
  }
  const clientIp = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  headers.set("x-forwarded-for", clientIp || "127.0.0.1");
  const secret = process.env.PROXY_SHARED_SECRET;
  if (secret) headers.set("x-proxy-auth", secret);
  else headers.delete("x-proxy-auth"); // never pass on one a client made up
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: "/api/:path*" };
