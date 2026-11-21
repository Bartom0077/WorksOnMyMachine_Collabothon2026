import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { approvedDecision, chatProposal, chatRemovalProposal, chatRemoved, greenDashboard } from "../fixtures/data";
import { assistantPanel, assistantToggle, openAssistant } from "../fixtures/assistant";
import { mockApi } from "../fixtures/mockApi";

/** Design-review screenshots; only produced when SCREENSHOT_DIR is set. */
const outputDir = process.env.SCREENSHOT_DIR;

const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

const DESKTOP = "desktop-1440";

/** The workspace tabs, each captured once at desktop width. */
const WORKSPACE_TABS = [
  { file: "options", name: /Options/ },
  { file: "advice", name: /CFO advice/ },
  { file: "plans", name: /My plans/ },
  { file: "history", name: /History/ },
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
      // The first screen: the assistant is closed.
      await page.screenshot({ path: join(directory, `red-closed-${viewport.name}.png`), fullPage: true });
      await page.screenshot({ path: join(directory, `red-closed-viewport-${viewport.name}.png`) });

      const assistant = await openAssistant(page);
      await page.screenshot({ path: join(directory, `assistant-opened-viewport-${viewport.name}.png`) });
      await page.getByRole("button", { name: "We are closing for holidays from 1 to 21 June" }).click();
      await expect(page.getByRole("group", { name: "Plan awaiting your confirmation" })).toBeVisible();
      await page.screenshot({ path: join(directory, `red-${viewport.name}.png`), fullPage: true });
      await page.screenshot({ path: join(directory, `red-viewport-${viewport.name}.png`) });

      // Closed with a proposal waiting: the toggle carries the attention dot.
      await assistant.getByRole("button", { name: "Close assistant" }).click();
      await expect(assistantToggle(page)).toHaveAccessibleName(/needs your confirmation/);
      await expect(assistantToggle(page)).toHaveCSS("background-color", "rgb(255, 204, 51)"); // transition over
      await page.screenshot({ path: join(directory, `red-closed-attention-viewport-${viewport.name}.png`) });
      await openAssistant(page);

      if (viewport.name === DESKTOP) {
        for (const tab of WORKSPACE_TABS) {
          await page.getByRole("tab", { name: tab.name }).click();
          await expect(page.getByRole("tabpanel")).toBeVisible();
          await page.getByRole("tablist").evaluate((list) => list.scrollIntoView({ block: "start" }));
          await page.screenshot({ path: join(directory, `tab-${tab.file}-${viewport.name}.png`) });
        }
        await page.getByRole("tab", { name: /Options/ }).click();

        await page.getByText("Why?", { exact: true }).click();
        await page.getByText("Details and table", { exact: true }).click();
        await page.getByText("Details of option E").click();
        await page.screenshot({ path: join(directory, `red-details-open-${viewport.name}.png`), fullPage: true });
      }

      await page.getByRole("button", { name: "Approve option E" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.screenshot({ path: join(directory, `approve-dialog-${viewport.name}.png`) });

      await page.unrouteAll();
      await mockApi(page, { dashboard: greenDashboard, events: [] });
      await page.goto("/");
      await expect(page.getByText("No action needed")).toBeVisible();
      await expect(page.getByRole("list", { name: "Chart legend" })).toBeVisible();
      await page.screenshot({ path: join(directory, `green-${viewport.name}.png`), fullPage: true });
      // The open assistant was remembered across the reload; the calm first screen is the closed one.
      await assistantPanel(page).getByRole("button", { name: "Close assistant" }).click();
      await expect(assistantToggle(page)).toHaveCSS("background-color", "rgb(255, 204, 51)"); // transition over
      await page.screenshot({ path: join(directory, `green-closed-${viewport.name}.png`), fullPage: true });
    });

    test(`chat removal card at ${viewport.name}`, async ({ page }) => {
      const directory = outputDir ?? "";
      mkdirSync(directory, { recursive: true });
      await page.setViewportSize({ width: viewport.width, height: viewport.height });

      await mockApi(page, { chatQueue: [chatRemovalProposal, chatRemoved] });
      await page.goto("/");
      const chat = await openAssistant(page);
      await page.getByLabel("Message to the assistant").fill("Please remove the oven purchase");
      await page.getByLabel("Message to the assistant").press("Enter");
      const removal = chat.getByRole("group", { name: "Plan awaiting removal" });
      await expect(removal).toBeVisible();
      await chat.screenshot({ path: join(directory, `removal-card-${viewport.name}.png`) });

      await removal.getByRole("button", { name: "Yes, remove it" }).click();
      await expect(chat.getByText("Removed from your plans: New pizza oven")).toBeVisible();
      await chat.screenshot({ path: join(directory, `removal-done-${viewport.name}.png`) });
    });
  }
});
