import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { approvedDecision, chatProposal, greenDashboard } from "../fixtures/data";
import { mockApi } from "../fixtures/mockApi";

/** Design-review screenshots; only produced when SCREENSHOT_DIR is set. */
const outputDir = process.env.SCREENSHOT_DIR;

const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

test.describe("screenshots", () => {
  test.skip(outputDir === undefined, "Set SCREENSHOT_DIR to capture design-review screenshots.");

  for (const viewport of VIEWPORTS) {
    test(`RED and GREEN states at ${viewport.name}`, async ({ page }) => {
      const directory = outputDir ?? "";
      mkdirSync(directory, { recursive: true });
      await page.setViewportSize({ width: viewport.width, height: viewport.height });

      await mockApi(page, { decisions: [approvedDecision], chatQueue: [chatProposal] });
      await page.goto("/");
      await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "We are closing for holidays from 1 to 21 June" }).click();
      await expect(page.getByRole("group", { name: "Plan awaiting your confirmation" })).toBeVisible();
      await page.screenshot({ path: join(directory, `red-${viewport.name}.png`), fullPage: true });

      await page.getByRole("button", { name: "Approve option E" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.screenshot({ path: join(directory, `approve-dialog-${viewport.name}.png`) });

      await page.unrouteAll();
      await mockApi(page, { dashboard: greenDashboard, events: [] });
      await page.goto("/");
      await expect(page.getByText("No action needed")).toBeVisible();
      await page.screenshot({ path: join(directory, `green-${viewport.name}.png`), fullPage: true });
    });
  }
});
