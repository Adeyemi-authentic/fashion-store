import { defineConfig, devices } from "@playwright/test";

// Runs against a production build (`npm run build && npm start`) with the API on :8000.
// "iphone-safari" uses Playwright's WebKit. Its macOS 14 build crashes on launch on this
// machine, so the real-Safari check is e2e/safari.mjs (`npm run e2e:safari`) instead.
export default defineConfig({
  testDir: "e2e",
  // Deletes e2e accounts/products/orders once, after every spec has finished. (Per-spec
  // afterAll hooks ran in parallel and could return the same order's stock twice.)
  globalTeardown: "./e2e/global-teardown.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 }, // local API -> Neon (Ohio) is ~1 s per call
  retries: 0,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", trace: "retain-on-failure" },
  projects: [
    { name: "android-chrome", use: { ...devices["Pixel 7"] } },
    { name: "iphone-safari", use: { ...devices["iPhone 15"] } },
  ],
  webServer: {
    command: "npm run start",
    url: "http://localhost:3000",
    reuseExistingServer: true,
  },
});
