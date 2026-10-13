import { expect, type Page, test } from "@playwright/test";
import { addFirstInStockItem, signUp, uniqueEmail } from "./helpers";

// Drives Paystack's real test-mode checkout page (no real money). Locally Paystack can't
// reach our webhook, so these runs prove the other path: the callback page asks the API,
// the API verifies with Paystack and applies the same apply_successful_charge().
test.describe.configure({ mode: "serial" });
test.setTimeout(180_000);

async function payOnPaystack(page: Page, outcome: "Success" | "Declined") {
  await page.waitForURL(/checkout\.paystack\.com/, { timeout: 60_000 });
  await page.getByText("Pay with Card").click();
  await page.getByText(outcome, { exact: true }).click();
  await page.getByRole("button", { name: /^Pay NGN/ }).click();
}

test("a guest pays with Paystack's test card and comes back to a paid order", async ({ page }) => {
  const item = await addFirstInStockItem(page);
  await page.getByRole("button", { name: /Open bag/ }).click();
  await page.getByRole("dialog").getByRole("link", { name: "Checkout" }).click();

  await expect(page.getByTestId("checkout-lines")).toContainText(item);
  const total = await page.getByTestId("checkout-total").innerText();
  await page.getByLabel("Email for your receipt").fill(uniqueEmail());
  await page.getByRole("button", { name: /Pay .* with Paystack/ }).click();

  await payOnPaystack(page, "Success");

  // Back on our site. The URL alone proves nothing; the status comes from the API.
  await page.waitForURL(/\/checkout\/callback\?/, { timeout: 60_000 });
  await expect(page.getByTestId("order-status")).toHaveAttribute("data-status", "paid", { timeout: 60_000 });
  await expect(page.getByRole("status")).toHaveText("Payment received. Thank you!");
  await expect(page.getByTestId("order-status")).toContainText(total);
  // The bought item has left the bag.
  await expect(page.getByRole("button", { name: /Open bag, 0 items/ })).toBeVisible({ timeout: 30_000 });
});

test("a signed-in customer's paid order shows in their account", async ({ page }) => {
  await signUp(page, uniqueEmail());
  await expect(page.getByRole("heading", { name: "Your account" })).toBeVisible({ timeout: 30_000 });
  const item = await addFirstInStockItem(page);
  await page.goto("/checkout");
  await expect(page.getByText(/Receipt goes to e2e-/)).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /Pay .* with Paystack/ }).click();
  await payOnPaystack(page, "Success");
  await expect(page.getByTestId("order-status")).toHaveAttribute("data-status", "paid", { timeout: 60_000 });

  await page.goto("/account");
  await expect(page.getByTestId("my-orders")).toContainText(item, { timeout: 30_000 });
  await expect(page.getByTestId("my-orders")).toContainText("paid");
});
