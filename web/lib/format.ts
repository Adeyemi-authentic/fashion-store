const naira = new Intl.NumberFormat("en-NG", {
  style: "currency",
  currency: "NGN",
  maximumFractionDigits: 0,
});

/** Integer kobo -> "₦45,000". Prices are whole naira, so no decimals are shown. */
export function formatNaira(kobo: number): string {
  return naira.format(kobo / 100);
}
