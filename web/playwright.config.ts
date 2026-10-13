import { defineConfig, devices } from "@playwright/test";

// Runs against a production build (`npm run build && npm start`) with the API on :8000.
// WebKit stands in for Safari: it applies the same cookie rules (ITP) that ADR-005 is about.
export default defineConfig({
  testDir: "e2e",
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
