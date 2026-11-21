import { defineConfig, devices } from "@playwright/test";

const PORT = 5173;
// Set E2E_BASE_URL to test a deployed instance (e.g. the OpenShift Route) instead of the local dev server.
const baseURL = process.env.E2E_BASE_URL || `http://localhost:${PORT}`;

export default defineConfig({
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    // Backend mocked with page.route(); needs only the frontend. Run with `npm run test:e2e`.
    { name: "mocked", testDir: "./e2e/mocked", use: { ...devices["Desktop Chrome"] } },
    // Real end-to-end against a deployed stack (real backend + LLM). Run with `E2E_BASE_URL=... npm run test:e2e:live`.
    {
      name: "live",
      testDir: "./e2e/live",
      timeout: 240_000,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `npm run dev -- --port ${PORT} --strictPort`,
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
