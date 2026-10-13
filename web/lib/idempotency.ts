/** A random key for one checkout attempt. crypto.randomUUID needs a secure context (https or
 * localhost), so fall back to getRandomValues for e.g. a phone testing over the LAN. */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
