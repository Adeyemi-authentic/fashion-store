import { expect, test } from "@playwright/test";

test("browse a category, pick a size, add to bag, change qty, survive a reload", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // Home -> category (via the scrollable category nav)
  await page.getByRole("navigation", { name: "Categories" }).getByRole("link", { name: "Adire" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Adire" })).toBeVisible();

  // Only in-stock items, so the product we open has at least one size to buy
  await page.getByLabel("In stock").check();
  await expect(page).toHaveURL(/in_stock=1/);
  await expect(page.getByText("Sold out", { exact: true })).toHaveCount(0);
  await page.getByTestId("product-card").first().click();

  // Product page: pick the first size that isn't sold out (unless it's one-size)
  const addButton = page.getByRole("button", { name: "Add to bag" });
  await expect(addButton).toBeVisible();
  const productName = await page.getByRole("heading", { level: 1 }).innerText();
  let foundStock = false;
  for (const colour of await page.getByRole("group", { name: /Colour/ }).getByRole("button").all()) {
    await colour.click();
    const size = page.getByRole("group", { name: "Size" }).getByRole("button", { disabled: false }).first();
    if (await size.count()) {
      await size.click();
      foundStock = true;
      break;
    }
  }
  expect(foundStock, "an in-stock product should have a size to buy").toBe(true);
  await addButton.click();

  // The drawer opens with the item
  const drawer = page.getByRole("dialog", { name: /Your bag/ });
  await expect(drawer).toBeVisible();
  const line = drawer.getByTestId("cart-line").filter({ hasText: productName });
  await expect(line).toBeVisible();
  await expect(line.getByTestId("cart-line-qty")).toHaveText("1");
  await expect(page.getByRole("button", { name: /Open bag, 1 item/ })).toBeAttached();

  const increase = line.getByRole("button", { name: /Increase quantity/ });
  if (await increase.isEnabled()) {
    await increase.click();
    await expect(line.getByTestId("cart-line-qty")).toHaveText("2");
  }
  const qtyBefore = await line.getByTestId("cart-line-qty").innerText();

  // The cart lives on the server behind a first-party cookie, so it survives a reload
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await page.reload();
  await expect(page.getByRole("button", { name: new RegExp(`Open bag, ${qtyBefore} item`) })).toBeVisible();
  const cookies = await page.context().cookies();
  const cart = cookies.find((c) => c.name === "cart_token");
  expect(cart?.httpOnly).toBe(true);
  expect(cart?.domain).toBe("localhost"); // set on the storefront origin, not the API's

  // Remove it again
  await page.getByRole("button", { name: /Open bag/ }).click();
  await drawer.getByTestId("cart-line").filter({ hasText: productName }).getByRole("button", { name: "Remove" }).click();
  await expect(drawer.getByText("Your bag is empty.")).toBeVisible();
});

test("asking for more than is in stock shows a message instead of failing", async ({ page, request }) => {
  // Find a variant with little stock through the same /api proxy the browser uses
  const listing = await (await request.get("/api/products?in_stock=true&limit=60")).json();
  let target: { slug: string; size: string; colour: string; available: number } | null = null;
  for (const card of listing.items) {
    const detail = await (await request.get(`/api/products/${card.slug}`)).json();
    const v = detail.variants.find((x: { available: number }) => x.available > 0 && x.available <= 3);
    if (v) {
      target = { slug: card.slug, size: v.size, colour: v.colour, available: v.available };
      break;
    }
  }
  test.skip(!target, "no low-stock variant in the seed");
  const t = target!;

  await page.goto(`/p/${t.slug}`);
  await page.getByRole("group", { name: /Colour/ }).getByRole("button", { name: t.colour, exact: true }).click();
  if (t.size !== "One Size") {
    await page.getByRole("group", { name: "Size" }).getByRole("button", { name: t.size, exact: true }).click();
  }
  await expect(page.getByTestId("stock")).toHaveText(`Only ${t.available} left`);
  for (let i = 0; i < t.available; i++) {
    await page.getByRole("button", { name: "Add to bag" }).click();
    await expect(page.getByRole("dialog", { name: /Your bag/ })).toBeVisible();
    await page.keyboard.press("Escape");
  }
  await page.getByRole("button", { name: "Add to bag" }).click();
  await expect(page.locator("main").getByRole("alert")).toHaveText(`Only ${t.available} left`);
});
