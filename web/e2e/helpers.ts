import { execSync } from "node:child_process";
import path from "node:path";
import type { Page } from "@playwright/test";

const API_DIR = path.resolve(__dirname, "../../api");
export const PASSWORD = "e2e password 123";

export function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

/** Shell access is the only way to make an admin (there's no API route for it). */
export function makeAdmin(email: string): void {
  execSync(`uv run python -m scripts.make_admin ${email}`, { cwd: API_DIR, stdio: "pipe" });
}

export function cleanupTestData(): void {
  execSync("uv run python -m scripts.e2e_cleanup", { cwd: API_DIR, stdio: "pipe" });
}

export async function signUp(page: Page, email: string, next?: string): Promise<void> {
  await page.goto(`/account/signup${next ? `?next=${encodeURIComponent(next)}` : ""}`);
  const main = page.locator("main"); // see accounts-admin.spec.ts on streamed hidden copies
  await main.getByLabel("Email").fill(email);
  await main.getByLabel("Password").fill(PASSWORD);
  await main.getByRole("button", { name: "Create account" }).click();
}

export async function addFirstInStockItem(page: Page): Promise<string> {
  await page.goto("/c/adire?in_stock=1");
  await page.getByTestId("product-card").first().click();
  const name = await page.getByRole("heading", { level: 1 }).innerText();
  const size = page.getByRole("group", { name: "Size" }).getByRole("button", { disabled: false }).first();
  if (await size.count()) await size.click();
  await page.getByRole("button", { name: "Add to bag" }).click();
  await page.getByRole("dialog", { name: /Your bag/ }).waitFor();
  await page.keyboard.press("Escape");
  return name;
}
