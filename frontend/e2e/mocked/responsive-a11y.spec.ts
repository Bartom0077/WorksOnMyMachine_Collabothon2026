import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";
import { approvedDecision, chatProposal, chatRemovalProposal, chatRemoved, greenDashboard } from "../fixtures/data";
import { mockApi } from "../fixtures/mockApi";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([]);
}

function workspaceTab(page: Page, name: RegExp) {
  return page.getByRole("tablist", { name: "Decisions and plans" }).getByRole("tab", { name });
}

/** Selects each workspace tab in turn and checks the page with that panel on screen. */
async function expectNoAxeViolationsInEachTab(page: Page, names: RegExp[]) {
  for (const name of names) {
    const tab = workspaceTab(page, name);
    await tab.click();
    await expect(page.getByRole("tabpanel")).toHaveAccessibleName(name);
    await expectNoAxeViolations(page);
  }
}

const ALL_TABS = [/Options/, /CFO advice/, /My plans/, /History/];

async function loadRedState(page: Page) {
  await mockApi(page, { decisions: [approvedDecision], chatQueue: [chatProposal] });
  await page.goto("/");
  await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();
}

test.describe("accessibility", () => {
  test("RED state with chat confirmation has no WCAG A/AA violations", async ({ page }) => {
    await loadRedState(page);
    await page.getByRole("button", { name: "We are closing for holidays from 1 to 21 June" }).click();
    await expect(page.getByRole("group", { name: "Plan awaiting your confirmation" })).toBeVisible();
    await page.getByText("Why?", { exact: true }).click();
    await expect(page.getByRole("row", { name: /Salaries/ })).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("every workspace tab has no WCAG A/AA violations", async ({ page }) => {
    await loadRedState(page);
    await expectNoAxeViolationsInEachTab(page, ALL_TABS);
  });

  test("opened disclosures have no WCAG A/AA violations", async ({ page }) => {
    await loadRedState(page);
    await page.getByText("Why?", { exact: true }).click();
    await page.getByText("Details and table", { exact: true }).click();
    await page.getByText("Details of option E").click();
    await expect(page.getByRole("region", { name: "Forecast table" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Reject option E" })).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("chat removal card and its outcome have no WCAG A/AA violations", async ({ page }) => {
    await mockApi(page, { chatQueue: [chatRemovalProposal, chatRemoved] });
    await page.goto("/");
    await page.getByLabel("Message to the assistant").fill("Please remove the oven purchase");
    await page.getByLabel("Message to the assistant").press("Enter");
    const removal = page.getByRole("group", { name: "Plan awaiting removal" });
    await expect(removal).toBeVisible();
    await expectNoAxeViolations(page);

    await removal.getByRole("button", { name: "Yes, remove it" }).click();
    await expect(page.getByText("Removed from your plans: New pizza oven")).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("approval dialog has no WCAG A/AA violations and traps focus", async ({ page }) => {
    await loadRedState(page);
    await page.getByRole("button", { name: "Approve option E" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expectNoAxeViolations(page);

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
  });

  test("GREEN state has no WCAG A/AA violations", async ({ page }) => {
    await mockApi(page, { dashboard: greenDashboard, events: [] });
    await page.goto("/");
    await expect(page.getByText("No action needed")).toBeVisible();
    await expectNoAxeViolations(page);
    await expectNoAxeViolationsInEachTab(page, [/CFO advice/, /My plans/, /History/]);
  });

  test("page structure exposes landmarks and a keyboard path to an option", async ({ page }) => {
    await loadRedState(page);
    await expect(page.getByRole("banner")).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Assistant" })).toBeVisible();

    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();

    // Moving focus through a card leaves the chart alone; the preview button is the explicit, keyboard-operable way.
    const legend = page.getByRole("list", { name: "Chart legend" });
    await page.getByRole("article", { name: /Option A/ }).getByRole("button", { name: /Preview on chart/ }).focus();
    await expect(legend).toContainText("Forecast with option E");
    await page.keyboard.press("Enter");
    await expect(legend).toContainText("Forecast with option A");
  });

  test("the tab list is a single Tab stop that leads into its panel", async ({ page }) => {
    await loadRedState(page);

    await workspaceTab(page, /Options/).focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(workspaceTab(page, /My plans/)).toBeFocused();

    // Tab leaves the tab list for the panel instead of walking through the remaining tabs.
    await page.keyboard.press("Tab");
    await expect(workspaceTab(page, /History/)).not.toBeFocused();
    const focusInPanel = await page
      .getByRole("tabpanel")
      .evaluate((panel) => panel.contains(document.activeElement));
    expect(focusInPanel).toBe(true);

    // Shift+Tab returns to the selected tab, not to the first one.
    await page.keyboard.press("Shift+Tab");
    await expect(workspaceTab(page, /My plans/)).toBeFocused();
  });

  test("a panel without controls is still a Tab stop", async ({ page }) => {
    await mockApi(page, { events: [], decisions: [] });
    await page.goto("/");

    for (const name of [/My plans/, /History/]) {
      await workspaceTab(page, name).click();
      await page.keyboard.press("Tab");
      await expect(page.getByRole("tabpanel", { name })).toBeFocused();
    }
  });
});

test.describe("mobile (375 px)", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("stacks the layout without horizontal page scroll", async ({ page }) => {
    await loadRedState(page);

    await expect(page.getByText("Status: Shortfall ahead")).toBeVisible();
    await expect(page.getByRole("article")).toHaveCount(4);
    await expect(page.getByRole("link", { name: "Tell the assistant about your plans" })).toBeVisible();
    await expect(page.getByLabel("Message to the assistant")).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await expectNoAxeViolations(page);
  });

  test("every workspace tab is reachable and fits the phone width", async ({ page }) => {
    await loadRedState(page);

    for (const name of ALL_TABS) {
      const tab = workspaceTab(page, name);
      // The tab list wraps instead of scrolling sideways: once scrolled to, each tab is entirely on screen.
      await tab.scrollIntoViewIfNeeded();
      await expect(tab).toBeInViewport({ ratio: 1 });
      await tab.click();
      await expect(page.getByRole("tabpanel")).toHaveAccessibleName(name);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `horizontal page scroll in tab ${name}`).toBeLessThanOrEqual(0);
      await expectNoAxeViolations(page);
    }
  });
});
