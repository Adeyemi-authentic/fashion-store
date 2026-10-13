export const inputClass =
  "mt-1 h-10 w-full rounded-lg border border-line bg-white px-3 text-sm focus:border-ink focus:outline-none";

/** "45,000" or "45000.50" (naira) -> integer kobo, or null if it isn't a positive amount. */
export function nairaToKobo(text: string): number | null {
  const cleaned = text.replace(/[,\s₦]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  const [whole, frac = ""] = cleaned.split(".");
  const kobo = Number(whole) * 100 + Number(frac.padEnd(2, "0"));
  return kobo > 0 ? kobo : null;
}

export function koboToNaira(kobo: number): string {
  return kobo % 100 === 0 ? String(kobo / 100) : (kobo / 100).toFixed(2);
}
