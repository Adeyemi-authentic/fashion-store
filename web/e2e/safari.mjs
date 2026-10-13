// Real Safari check for ADR-005 (Playwright's WebKit build doesn't run on macOS 14).
// One-time setup:  safaridriver --enable   (asks for an admin password)
// Run:             npm run e2e:safari       (needs `npm start` on :3000 and the API on :8000)
import assert from "node:assert/strict";
import { Builder, By, until } from "selenium-webdriver";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3000";

// Pick an in-stock product through the same /api proxy the browser uses.
const listing = await (await fetch(`${BASE}/api/products?in_stock=true&limit=5`)).json();
const slug = listing.items[0].slug;

const driver = await new Builder().forBrowser("safari").build();
try {
  await driver.manage().window().setRect({ width: 390, height: 844 }); // phone-width window
  await driver.get(`${BASE}/p/${slug}`);
  const addButton = await driver.wait(until.elementLocated(By.xpath("//button[normalize-space()='Add to bag']")), 15000);

  // Choose a colour + size that isn't sold out (one-size products have no size buttons).
  const colours = await driver.findElements(By.xpath("//fieldset[legend[starts-with(normalize-space(),'Colour')]]//button"));
  let picked = false;
  for (const colour of colours) {
    await colour.click();
    const sizes = await driver.findElements(By.xpath("//fieldset[legend[normalize-space()='Size']]//button[not(@disabled)]"));
    const sizeGroups = await driver.findElements(By.xpath("//fieldset[legend[normalize-space()='Size']]"));
    if (sizeGroups.length === 0) { picked = true; break; }
    if (sizes.length) { await sizes[0].click(); picked = true; break; }
  }
  assert.ok(picked, "no in-stock variant to pick");
  await addButton.click();

  const line = By.css("dialog[open] [data-testid='cart-line']");
  await driver.wait(until.elementLocated(line), 15000);
  console.log("✓ added to bag; drawer open with 1 line");

  // The cookie must be first-party (set on the storefront origin) and HttpOnly.
  const cookie = await driver.manage().getCookie("cart_token");
  assert.ok(cookie, "Safari did not keep the cart_token cookie");
  assert.equal(cookie.httpOnly, true);
  assert.equal(cookie.domain.replace(/^\./, ""), new URL(BASE).hostname);
  console.log(`✓ cart_token cookie kept by Safari (domain ${cookie.domain}, HttpOnly)`);

  // Survives a reload: the cart is read back from the server via the cookie.
  await driver.navigate().refresh();
  await driver.wait(until.elementLocated(By.css("button[aria-label^='Open bag, 1 item']")), 15000);
  console.log("✓ cart survives a reload");

  // Clean up.
  await driver.findElement(By.css("button[aria-label^='Open bag']")).click();
  await driver.wait(until.elementLocated(By.xpath("//dialog[@open]//button[normalize-space()='Remove']")), 15000).click();
  await driver.wait(until.elementLocated(By.xpath("//dialog[@open]//p[normalize-space()='Your bag is empty.']")), 15000);
  console.log("✓ removed; bag empty");
  console.log(`Safari: browse-to-cart OK (${slug})`);
} finally {
  await driver.quit();
}
