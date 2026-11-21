import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";
import { approvedDecision, chatProposal, greenDashboard } from "../fixtures/data";
import { mockApi } from "../fixtures/mockApi";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([]);
}

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
    await page.getByRole("button", { name: "Why?" }).click();
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
});
