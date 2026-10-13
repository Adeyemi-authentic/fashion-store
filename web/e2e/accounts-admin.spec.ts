import { expect, test } from "@playwright/test";
import { addFirstInStockItem, makeAdmin, PASSWORD, signUp, uniqueEmail } from "./helpers";


test("a guest's bag survives sign-up; customers can't use admin; sign-out ends the session", async ({ page }) => {
  const itemName = await addFirstInStockItem(page);

  await signUp(page, uniqueEmail());
  await expect(page.getByRole("heading", { name: "Your account" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Open bag, 1 item/ })).toBeVisible(); // merged at sign-up
  await page.getByRole("button", { name: /Open bag/ }).click();
  await expect(page.getByRole("dialog").getByText(itemName)).toBeVisible();
  await page.keyboard.press("Escape");

  // The admin screens refuse, and so does the API itself (the real check).
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Admins only" })).toBeVisible();
  const api = await page.request.get("/api/admin/products");
  expect(api.status()).toBe(403);
  const write = await page.request.post("/api/admin/products", { data: { name: "E2E hack" } });
  expect(write.status()).toBe(403);

  await page.goto("/account");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL("/"); // sign-out finishes with a full load of the home page
  await expect(page.getByRole("button", { name: /Open bag, 0 items/ })).toBeVisible();
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
});

// Admin writes make ~25 database round trips. From Lagos to Neon in Ohio that is ~6 s per
// save (vs ~1 ms per trip once the API runs in Neon's region, Phase 9), so allow for it.
const SAVE = { timeout: 30_000 };

test("an admin creates, publishes, restocks and archives a product, and the shop updates at once", async ({ page }) => {
  test.setTimeout(180_000);
  const email = uniqueEmail();
  expect((await page.request.post("/api/auth/signup", { data: { email, password: PASSWORD } })).status()).toBe(201);
  makeAdmin(email);
  await page.request.post("/api/auth/logout");

  // Signing in from the admin page brings you back to it.
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/account\/login\?next=(%2F|\/)admin$/);
  // Scoped to <main>: while a streamed page swaps in, React briefly keeps a hidden copy of
  // the new content outside it.
  const main = page.locator("main");
  await main.getByLabel("Email").fill(email);
  await main.getByLabel("Password").fill(PASSWORD);
  await main.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Products" })).toBeVisible();

  // Create and publish
  const name = `E2E Kaftan ${Date.now()}`;
  await page.getByRole("link", { name: "New product" }).click();
  // Wait for the new page: until then the old one (with its "Search by name" box) is still
  // showing, and a loose label match would type into that.
  await expect(page.getByRole("heading", { level: 1, name: "New product" })).toBeVisible();
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Category").selectOption("adire");
  await page.getByLabel(/Image path/).fill("/images/products/adire-agege-kaftan-dress.webp");
  await page.getByLabel("Variant 1 size").fill("M");
  await page.getByLabel("Variant 1 colour").fill("Deep Indigo");
  await page.getByLabel("Variant 1 price in naira").fill("45,000");
  await page.getByLabel("Variant 1 stock").fill("2");
  await page.getByLabel(/Publish now/).check();
  await page.getByRole("button", { name: "Create product" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible(SAVE);
  await expect(page.getByTestId("variant-stock")).toContainText("On hand 2");

  // The cached category listing shows it straight away (updateTag on save).
  await page.goto("/c/adire");
  await expect(page.getByTestId("product-card").filter({ hasText: name })).toBeVisible();
  await page.getByTestId("product-card").filter({ hasText: name }).click();
  await expect(page.getByTestId("price")).toHaveText("₦45,000");
  await page.goBack();
  await page.goBack();

  // Restock with a delta, and change the price
  await page.getByLabel(/Stock change for/).fill("3");
  await page.getByLabel(/Reason for/).fill("delivery");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByTestId("variant-stock")).toContainText("On hand 5", SAVE);
  await page.getByLabel(/Price for/).fill("50000");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Price saved")).toBeVisible(SAVE);
  await expect(page.getByText("now ₦50,000")).toBeVisible(SAVE);

  // Removing more than is on hand is refused with a message, not an error page
  await page.getByLabel(/Stock change for/).fill("-9");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("Can't remove more than the 5 on hand", SAVE);

  // Archive: gone from the shop, still in admin
  await page.getByRole("button", { name: "Archive product" }).click();
  await page.getByRole("button", { name: "Yes, archive it" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Archived" })).toBeVisible(SAVE);
  await page.goto("/c/adire");
  await expect(page.getByRole("heading", { level: 1, name: "Adire" })).toBeVisible();
  await expect(page.getByTestId("product-card").filter({ hasText: name })).toHaveCount(0);

  // Orders page loads with its status filter
  await page.goto("/admin/orders?status=paid");
  await expect(page.getByRole("heading", { name: "Orders" })).toBeVisible();
  await expect(page.getByRole("link", { name: "paid", exact: true })).toHaveAttribute("aria-current", "page");
});
