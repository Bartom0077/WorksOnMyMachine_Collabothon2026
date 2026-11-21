import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { type Locator, type Page, expect, test } from "@playwright/test";

/**
 * The demo story (ROZWIAZANIE.md §13 and §15) against a deployed stack: real backend, real database, real LLM.
 *
 *   E2E_BASE_URL=https://<route> npm run test:e2e:live
 *
 * The tests share one page and run in order; they start and end with "Reset demo", which DELETES all planned
 * events and decisions of that deployment. Figures asserted here are the ones the forecasting code computes
 * from the 2025 bank data; the LLM's wording is never asserted.
 * Set SCREENSHOT_DIR to capture a full-page screenshot at every step (the key states also at 375 px).
 */

const LLM_TIMEOUT_MS = 150_000;
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 375, height: 812 };
const HOLIDAY_PLAN = "We are closing for holidays from 1 to 21 June.";
const REMOVAL_REQUEST = "Please remove the holiday closure from my plans.";

const FIGURES = {
  balanceToday: "€3,205.69",
  lowestWithoutPlans: "-€325.55",
  lowestWithClosure: "-€15,728.42",
  lowestDate: "on 1 Jul 2025",
  optionIds: ["A", "B", "C", "E"],
  decemberBalance: "€33,266.35",
  decemberSurplus: "€27,000.00",
};

const screenshotDir = process.env.SCREENSHOT_DIR;

test.describe.configure({ mode: "serial" });

test.describe("demo story on the deployed application", () => {
  test.skip(process.env.E2E_BASE_URL === undefined, "Set E2E_BASE_URL to the deployed application.");

  let page: Page;
  let recommendedOptionId: string;

  async function capture(name: string): Promise<void> {
    if (screenshotDir === undefined) return;
    mkdirSync(screenshotDir, { recursive: true });
    await page.mouse.move(0, 0); // no chart tooltip in the picture
    await page.screenshot({ path: join(screenshotDir, `${name}.png`), fullPage: true });
  }

  /** A key state of the story: captured at desktop width and on a phone. */
  async function captureKeyState(name: string): Promise<void> {
    if (screenshotDir === undefined) return;
    await capture(`${name}-desktop`);
    await page.setViewportSize(MOBILE);
    await capture(`${name}-mobile`);
    await page.setViewportSize(DESKTOP);
  }

  /** A figure of the status hero (the same amounts also appear in the chart caption). */
  function heroFigure(text: string): Locator {
    const hero = page.getByRole("region").filter({ has: page.getByText("Lowest projected balance", { exact: true }) });
    return hero.getByText(text, { exact: true });
  }

  function plans(): Locator {
    return page.getByRole("region", { name: "My plans" });
  }

  function explanationBadge(): Locator {
    return page.getByText(/^(AI explanation|Standard explanation \(AI unavailable\))$/);
  }

  /** The explanation is requested again for every new forecast; wait until the current one has arrived. */
  async function waitForExplanation(): Promise<string> {
    await expect(page.getByText("The assistant is analysing your forecast…")).toBeHidden({ timeout: LLM_TIMEOUT_MS });
    await expect(explanationBadge()).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    return explanationBadge().innerText();
  }

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage({ viewport: DESKTOP });
    page.on("requestfinished", (request) => {
      const path = new URL(request.url()).pathname;
      if (path === "/api/chat" || path === "/api/recommendation") {
        console.log(`[latency] POST ${path}: ${(request.timing().responseEnd / 1000).toFixed(1)} s`);
      }
    });
  });

  test.afterAll(async () => {
    const response = await page.request.post("/api/demo/reset");
    expect(response.status()).toBe(204);
    await page.close();
  });

  test("starts from a clean demo on 10 May 2025", async () => {
    await page.goto("/");
    await page.getByRole("button", { name: "Reset demo" }).click();
    await page.getByRole("dialog", { name: "Reset the demo?" }).getByRole("button", { name: "Delete plans and decisions" }).click();
    await expect(page.getByText("Demo reset: all plans and decisions were deleted.")).toBeVisible();

    await expect(page.getByLabel("Today")).toHaveValue("2025-05-10");
    await expect(page.getByText(/No plans yet/)).toBeVisible();
    await expect(page.getByText(/No decisions yet/)).toBeVisible();
    await expect(heroFigure(FIGURES.balanceToday)).toBeVisible();
    // The business is close to break-even: without any plans the forecast already dips just below zero.
    await expect(heroFigure(FIGURES.lowestWithoutPlans)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestDate)).toBeVisible();
    await expect(page.locator(".recharts-line").first()).toBeVisible();

    await waitForExplanation();
    await captureKeyState("01-initial");
  });

  test("the assistant recognises the holiday closure and asks before saving", async () => {
    const input = page.getByLabel("Message to the assistant");
    await input.fill(HOLIDAY_PLAN);
    await input.press("Enter");

    const confirmation = page.getByRole("group", { name: "Plan awaiting your confirmation" });
    await expect(confirmation).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    await expect(confirmation).toContainText("Closure");
    await expect(confirmation).toContainText("1 Jun – 21 Jun 2025");
    // Nothing is stored and nothing changes before the owner confirms.
    await expect(page.getByText(/No plans yet/)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestWithoutPlans)).toBeVisible();
    await capture("02-plan-awaiting-confirmation-desktop");
  });

  test("confirming the plan turns the outlook RED and lists the plan", async () => {
    await page.getByRole("button", { name: "Yes, include it" }).click();

    await expect(page.getByText("Forecast updated with your plans.")).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    await expect(heroFigure(FIGURES.lowestWithClosure)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestDate)).toBeVisible();
    await expect(page.getByText("Status: Shortfall ahead")).toBeVisible();

    const plan = plans().getByRole("listitem");
    await expect(plan).toHaveCount(1);
    await expect(plan).toContainText("Closure · 1 Jun – 21 Jun 2025");
    await expect(plan).toContainText("From chat");
    await plan.getByText("What you said").click();
    await expect(plan.getByText(HOLIDAY_PLAN)).toBeVisible();
  });

  test("explains the shortfall and offers rescue options with one recommendation", async () => {
    const badge = await waitForExplanation();
    test.info().annotations.push({ type: "explanation source", description: badge });
    console.log(`[explanation] ${badge}`);
    await expect(page.locator(".explanation")).not.toBeEmpty();

    await page.getByRole("button", { name: "Why?" }).click();
    await expect(page.getByRole("row", { name: /Staff salaries/ })).toContainText("Must be paid");
    await expect(page.getByRole("row", { name: /Transfer to Zurich/ })).toContainText("Can be postponed");

    const options = page.getByRole("article");
    await expect(options).toHaveCount(FIGURES.optionIds.length);
    for (const id of FIGURES.optionIds) {
      await expect(page.getByRole("article", { name: new RegExp(`^Option ${id}:`) })).toBeVisible();
    }
    const recommended = options.filter({ has: page.getByText("Recommended", { exact: true }) });
    await expect(recommended).toHaveCount(1);
    const title = await recommended.getByRole("heading").innerText();
    recommendedOptionId = /Option ([A-E]):/.exec(title)?.[1] ?? "";
    expect(FIGURES.optionIds).toContain(recommendedOptionId);
    console.log(`[recommended option] ${recommendedOptionId}`);
    // The recommended option is previewed on the chart by default.
    await expect(page.getByRole("list", { name: "Chart legend" })).toContainText(`Forecast with option ${recommendedOptionId}`);
    await capture("03-red-with-options-desktop");
  });

  test("the main screen fits a 375 px phone", async () => {
    await page.setViewportSize(MOBILE);
    await expect(heroFigure(FIGURES.lowestWithClosure)).toBeVisible();
    // Polled: the chart re-measures itself a moment after the viewport changes.
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
      .toBeLessThanOrEqual(0);
    await capture("03-red-with-options-mobile");
    await page.setViewportSize(DESKTOP);
  });

  test("previews another option on the chart", async () => {
    const otherId = FIGURES.optionIds.find((id) => id !== recommendedOptionId) ?? "";
    const other = page.getByRole("article", { name: new RegExp(`^Option ${otherId}:`) });
    await other.getByRole("button", { name: "Preview on chart" }).click();

    await expect(other.getByRole("button", { name: "Shown on chart" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("list", { name: "Chart legend" })).toContainText(`Forecast with option ${otherId}`);
    await expect(page.getByText(new RegExp(`With option ${otherId} \\(.+\\) the lowest point is`))).toBeVisible();
    await capture("05-option-preview-desktop");
  });

  test("approving the recommended option is simulated and turns the outlook GREEN", async () => {
    await page.getByRole("button", { name: `Approve option ${recommendedOptionId}` }).click();
    const dialog = page.getByRole("dialog", { name: new RegExp(`Approve option ${recommendedOptionId}`) });
    await expect(dialog.getByText("SIMULATED", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("listitem").first()).toContainText("(simulated)");
    await capture("06-approval-dialog-desktop");

    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();
    await expect(dialog).toBeHidden();

    const banner = page.getByRole("status").filter({ hasText: `Option ${recommendedOptionId} approved` });
    await expect(banner.getByRole("listitem").first()).toContainText("(simulated)");
    await expect(page.getByText("Status: On track")).toBeVisible();
    await expect(page.getByText("No action needed")).toBeVisible();
    await expect(page.getByRole("article")).toHaveCount(0);

    const historyRow = page.getByRole("row", { name: new RegExp(`${recommendedOptionId}: `) });
    await expect(historyRow).toHaveCount(1);
    await expect(historyRow).toContainText("Approved");
    await expect(historyRow).toContainText("SIMULATED");
    await expect(historyRow).toContainText(FIGURES.lowestWithClosure);
    await expect(plans().getByText("From an approved option").first()).toBeVisible();

    await waitForExplanation();
    await captureKeyState("07-approved-green");
  });

  test("removes the holiday plan from “My plans”", async () => {
    const rows = plans().getByRole("listitem");
    const before = await rows.count();
    const closure = rows.filter({ hasText: "Closure ·" });
    await closure.getByRole("button", { name: /^Remove plan:/ }).click();

    await expect(closure).toHaveCount(0);
    await expect(rows).toHaveCount(before - 1);
    await expect(page.getByText("Status: On track")).toBeVisible();
    await waitForExplanation();
    await capture("08-plan-removed-desktop");
  });

  test("at the end of December the surplus is pointed out", async () => {
    await page.getByLabel("Today").fill("2025-12-31");

    await expect(heroFigure(FIGURES.decemberBalance)).toBeVisible();
    await expect(page.getByText("Status: On track")).toBeVisible();
    await expect(page.getByText("No action needed")).toBeVisible();
    await expect(page.getByRole("list", { name: "Opportunities" })).toContainText(FIGURES.decemberSurplus);
    await waitForExplanation();
    await captureKeyState("09-december-surplus");
  });

  test("withdrawing the approved option removes all of its measures and keeps the decision on record", async () => {
    const measures = plans().getByRole("listitem").filter({ hasText: "From an approved option" });
    expect(await measures.count()).toBeGreaterThan(0);
    await measures.first().getByRole("button", { name: /^Withdraw option:/ }).click();
    const dialog = page.getByRole("dialog", { name: "Withdraw this approved option?" });
    await expect(dialog.getByRole("listitem")).toHaveCount(await measures.count());
    await dialog.getByRole("button", { name: "Withdraw option" }).click();

    await expect(dialog).toBeHidden();
    await expect(plans().getByText(/No plans yet/)).toBeVisible();
    const decisionRow = page.getByRole("row", { name: new RegExp(`${recommendedOptionId}: `) });
    await expect(decisionRow).toContainText("Withdrawn");
    await expect(decisionRow).toContainText("Reversed: no longer in the forecast");
    await capture("10-option-withdrawn-desktop");
  });

  // --- removing a plan through the chat (two turns: the assistant shows the plan, the owner confirms) ---

  test("a plan added in the chat is in the forecast again", async () => {
    expect((await page.request.post("/api/demo/reset")).status()).toBe(204);
    await page.goto("/"); // a fresh conversation on 10 May 2025
    await expect(page.getByText(/No plans yet/)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestWithoutPlans)).toBeVisible();

    const input = page.getByLabel("Message to the assistant");
    await input.fill(HOLIDAY_PLAN);
    await input.press("Enter");
    const confirmation = page.getByRole("group", { name: "Plan awaiting your confirmation" });
    await expect(confirmation).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    await confirmation.getByRole("button", { name: "Yes, include it" }).click();

    await expect(page.getByText("Forecast updated with your plans.")).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    await expect(plans().getByRole("listitem")).toHaveCount(1);
    await expect(heroFigure(FIGURES.lowestWithClosure)).toBeVisible();
    await waitForExplanation(); // the model is free again before the next chat message
  });

  test("asked to remove the plan, the assistant shows it and removes nothing yet", async () => {
    const input = page.getByLabel("Message to the assistant");
    await input.fill(REMOVAL_REQUEST);
    await input.press("Enter");

    const removal = page.getByRole("group", { name: "Plan awaiting removal" });
    await expect(removal).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    await expect(removal).toContainText("Closure");
    await expect(removal).toContainText("1 Jun – 21 Jun 2025");
    await expect(removal.getByRole("button", { name: "Yes, remove it" })).toBeEnabled();
    await expect(removal.getByRole("button", { name: "Keep it" })).toBeEnabled();
    // Nothing is removed and nothing changes before the owner confirms.
    const plan = plans().getByRole("listitem");
    await expect(plan).toHaveCount(1);
    await expect(plan).toContainText("Closure · 1 Jun – 21 Jun 2025");
    await expect(heroFigure(FIGURES.lowestWithClosure)).toBeVisible();
    const stored = (await (await page.request.get("/api/events")).json()) as { events: unknown[] };
    expect(stored.events).toHaveLength(1);
    await captureKeyState("11-removal-awaiting-confirmation");
  });

  test("confirming the removal takes the plan out and restores the earlier forecast", async () => {
    const removal = page.getByRole("group", { name: "Plan awaiting removal" });
    await removal.getByRole("button", { name: "Yes, remove it" }).click();

    await expect(page.getByText(/^Removed from your plans:/)).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    await expect(plans().getByText(/No plans yet/)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestWithoutPlans)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestDate)).toBeVisible();
    // The answered card cannot be used a second time.
    await expect(removal.getByRole("button", { name: "Yes, remove it" })).toBeDisabled();
    const stored = (await (await page.request.get("/api/events")).json()) as { events: unknown[] };
    expect(stored.events).toHaveLength(0);
    await captureKeyState("12-removed-through-chat");
  });
});
