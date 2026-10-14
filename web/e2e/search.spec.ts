import { expect, test } from "@playwright/test";

// Calls the real search: Claude reads the query and Voyage embeds it (cached by the API
// after the first run), so allow for the Lagos -> Neon round trips.
test("a messy query becomes filters the shopper can see; results respect them", async ({ page }) => {
  await page.goto("/search?q=wedding%20outfit%20under%2050k");
  await expect(page.getByRole("heading", { name: /Results for “wedding outfit under 50k”/ })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("Under ₦50,000")).toBeVisible();
  await expect(page.getByText("For wedding")).toBeVisible();
  const prices = await page.getByTestId("product-card").locator("p").allTextContents();
  expect(prices.length).toBeGreaterThan(0);
  for (const p of prices) expect(Number(p.replace(/[^\d]/g, ""))).toBeLessThanOrEqual(50_000);
});

test("the search box takes you to results, and a product page shows similar items", async ({ page }) => {
  await page.goto("/search");
  await page.getByRole("searchbox", { name: "Search" }).last().fill("linen shirt");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/\/search\?q=linen\+shirt/);
  const first = page.getByTestId("product-card").first();
  await expect(first).toContainText("Linen Shirt", { timeout: 30_000 });

  await first.click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Linen Shirt");
  const similar = page.getByRole("region", { name: "You might also like" });
  await expect(similar.getByTestId("product-card")).toHaveCount(4);
});
