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
 * Options, CFO advice, plans and history share one tab list and only the selected panel is rendered, so every
 * step selects the tab it reads from.
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
  // 31 Dec 2025: the income tax the prepayments do not cover, and what is left of the surplus after reserving it.
  decemberTaxReserve: "€14,849.65",
  decemberInvestmentDeposit: "€12,000.00",
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

  /** A figure of the status hero (the same amounts also appear in the option cards and the history). */
  function heroFigure(text: string): Locator {
    const hero = page.getByRole("region").filter({ has: page.getByText("Lowest projected balance", { exact: true }) });
    return hero.getByText(text, { exact: true });
  }

  type TabLabel = "Options" | "CFO advice" | "My plans" | "History";

  /** A workspace tab; its accessible name is the label followed by the count badge. */
  function tab(label: TabLabel): Locator {
    return page.getByRole("tablist", { name: "Decisions and plans" }).getByRole("tab", { name: new RegExp(`^${label}`) });
  }

  async function openTab(label: TabLabel): Promise<void> {
    await tab(label).click();
    await expect(tab(label)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel")).toHaveCount(1);
  }

  async function expectTabCount(label: TabLabel, count: number): Promise<void> {
    await expect(tab(label)).toHaveText(new RegExp(`^${label}\\s*${count}$`));
  }

  /** The "My plans" panel; select its tab first. */
  function plans(): Locator {
    return page.getByRole("region", { name: "My plans" });
  }

  /** The "History" panel (the section, not the scroll container of the same name); select its tab first. */
  function history(): Locator {
    return page.getByRole("region", { name: "Decision history" }).first();
  }

  /** The "CFO advice" panel; select its tab first. */
  function cfoRecommendations(): Locator {
    return page.getByRole("region", { name: "CFO recommendations" });
  }

  /** The deposit card of one advisor. Titles are the model's wording, so the card is found by a code-written fact. */
  function depositCard(advisor: string): Locator {
    return cfoRecommendations()
      .getByRole("group", { name: advisor, exact: true })
      .getByRole("group")
      .filter({ has: page.getByText("Interest earned", { exact: true }) });
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
      if (path === "/api/chat" || path === "/api/recommendation" || path === "/api/advice/recommendation") {
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
    await openTab("My plans");
    await expectTabCount("My plans", 0);
    await expect(plans().getByText(/No plans yet/)).toBeVisible();
    await openTab("History");
    await expectTabCount("History", 0);
    await expect(history().getByText(/No decisions yet/)).toBeVisible();
    await expect(heroFigure(FIGURES.balanceToday)).toBeVisible();
    // The business is close to break-even: without any plans the forecast already dips just below zero.
    await expect(heroFigure(FIGURES.lowestWithoutPlans)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestDate)).toBeVisible();
    await expect(page.locator(".recharts-line").first()).toBeVisible();

    await waitForExplanation();
    await expect(page.getByText(/^Figures: deterministic code on your bank transactions\./)).toBeVisible();
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
    await openTab("My plans");
    await expect(plans().getByText(/No plans yet/)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestWithoutPlans)).toBeVisible();
    await capture("02-plan-awaiting-confirmation-desktop");
  });

  test("confirming the plan turns the outlook RED and lists the plan", async () => {
    await page.getByRole("button", { name: "Yes, include it" }).click();

    await expect(page.getByText("Forecast updated with your plans.")).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    await expect(heroFigure(FIGURES.lowestWithClosure)).toBeVisible();
    await expect(heroFigure(FIGURES.lowestDate)).toBeVisible();
    await expect(page.getByText("Status: Shortfall ahead")).toBeVisible();

    // The tab the owner chose stays selected when the "Options" tab appears.
    await expect(tab("My plans")).toHaveAttribute("aria-selected", "true");
    await expectTabCount("My plans", 1);
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

    // The risk drivers are one click away, behind the "Why?" disclosure of the hero.
    const why = page.getByText("Why?", { exact: true });
    const salaries = page.getByRole("row", { name: /Staff salaries/ });
    await expect(salaries).toBeHidden();
    await why.click();
    await expect(salaries).toContainText("Must be paid");
    await expect(page.getByRole("row", { name: /Transfer to Zurich/ })).toContainText("Can be postponed");
    await why.click();
    await expect(salaries).toBeHidden();

    await openTab("Options");
    await expectTabCount("Options", FIGURES.optionIds.length);
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
    // The recommended option comes first, with its approval one click away; the rest of a card is under "Details".
    await expect(options.first()).toHaveAccessibleName(new RegExp(`^Option ${recommendedOptionId}:`));
    await expect(recommended.getByRole("button", { name: `Approve option ${recommendedOptionId}` })).toBeVisible();
    await expect(recommended.getByText("New lowest balance", { exact: true })).toBeVisible();
    const reject = recommended.getByRole("button", { name: `Reject option ${recommendedOptionId}` });
    await expect(reject).toBeHidden();
    const optionDetails = recommended.getByText(`Details of option ${recommendedOptionId}`, { exact: true });
    await optionDetails.click();
    await expect(reject).toBeVisible();
    await optionDetails.click();
    await expect(reject).toBeHidden();
    // The recommended option is previewed on the chart by default.
    await expect(page.getByRole("list", { name: "Chart legend" })).toContainText(`Forecast with option ${recommendedOptionId}`);
    await capture("03-red-with-options-desktop");
  });

  test("the workspace tabs can be operated from the keyboard", async () => {
    await tab("Options").focus();
    await page.keyboard.press("ArrowRight");
    await expect(tab("CFO advice")).toBeFocused();
    await expect(tab("CFO advice")).toHaveAttribute("aria-selected", "true");
    await expect(tab("Options")).toHaveAttribute("tabindex", "-1");
    await expect(cfoRecommendations()).toBeVisible();
    await expect(page.getByRole("article")).toHaveCount(0); // only the selected panel is rendered

    await page.keyboard.press("End");
    await expect(tab("History")).toBeFocused();
    await expect(history()).toBeVisible();
    await page.keyboard.press("ArrowRight"); // wraps around
    await expect(tab("Options")).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(tab("History")).toBeFocused();
    await page.keyboard.press("Home");
    await expect(tab("Options")).toBeFocused();
    await expect(tab("Options")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("article")).toHaveCount(FIGURES.optionIds.length);
  });

  test("the main screen fits a 375 px phone", async () => {
    await page.setViewportSize(MOBILE);
    await expect(heroFigure(FIGURES.lowestWithClosure)).toBeVisible();
    for (const label of ["Options", "CFO advice", "My plans", "History"] as const) {
      await expect(tab(label)).toBeVisible();
    }
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
    await openTab("Options");
    await other.getByRole("button", { name: `Preview on chart: option ${otherId}` }).click();

    await expect(other.getByRole("button", { name: `Shown on chart: option ${otherId}` })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByRole("list", { name: "Chart legend" })).toContainText(`Forecast with option ${otherId}`);
    // The chart's description and the forecast table sit behind a disclosure.
    const chartDetails = page.getByText("Details and table", { exact: true });
    const chartSummary = page.getByText(new RegExp(`With option ${otherId} \\(.+\\) the lowest point is`));
    await expect(chartSummary).toBeHidden();
    await chartDetails.click();
    await expect(chartSummary).toBeVisible();
    await expect(page.getByRole("region", { name: "Forecast table" })).toBeVisible();
    await chartDetails.click();
    await expect(chartSummary).toBeHidden();
    await capture("05-option-preview-desktop");
  });

  test("approving the recommended option is simulated and turns the outlook GREEN", async () => {
    await openTab("Options");
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
    // With nothing left to decide the "Options" tab is gone and the default tab takes over.
    await expect(tab("Options")).toHaveCount(0);
    await expect(tab("CFO advice")).toHaveAttribute("aria-selected", "true");
    await expect(cfoRecommendations()).toBeVisible();

    await openTab("History");
    await expectTabCount("History", 1);
    const historyRow = history().getByRole("row", { name: new RegExp(`${recommendedOptionId}: `) });
    await expect(historyRow).toHaveCount(1);
    await expect(historyRow).toContainText("Approved");
    await expect(historyRow).toContainText("SIMULATED");
    await expect(historyRow).toContainText(FIGURES.lowestWithClosure);
    await openTab("My plans");
    await expect(plans().getByText("From an approved option").first()).toBeVisible();

    await waitForExplanation();
    await captureKeyState("07-approved-green");
  });

  test("removes the holiday plan from “My plans”", async () => {
    await openTab("My plans");
    const rows = plans().getByRole("listitem");
    const before = await rows.count();
    const closure = rows.filter({ hasText: "Closure ·" });
    await closure.getByRole("button", { name: /^Remove plan:/ }).click();

    await expect(closure).toHaveCount(0);
    await expect(rows).toHaveCount(before - 1);
    await expectTabCount("My plans", before - 1);
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
    await openTab("My plans");
    const measures = plans().getByRole("listitem").filter({ hasText: "From an approved option" });
    expect(await measures.count()).toBeGreaterThan(0);
    await measures.first().getByRole("button", { name: /^Withdraw option:/ }).click();
    const dialog = page.getByRole("dialog", { name: "Withdraw this approved option?" });
    await expect(dialog.getByRole("listitem")).toHaveCount(await measures.count());
    await dialog.getByRole("button", { name: "Withdraw option" }).click();

    await expect(dialog).toBeHidden();
    await expect(plans().getByText(/No plans yet/)).toBeVisible();
    await expectTabCount("My plans", 0);
    await openTab("History");
    const decisionRow = history().getByRole("row", { name: new RegExp(`${recommendedOptionId}: `) });
    await expect(decisionRow).toContainText("Withdrawn");
    await expect(decisionRow).toContainText("Reversed: no longer in the forecast");
    await capture("10-option-withdrawn-desktop");
  });

  // --- removing a plan through the chat (two turns: the assistant shows the plan, the owner confirms) ---

  test("a plan added in the chat is in the forecast again", async () => {
    expect((await page.request.post("/api/demo/reset")).status()).toBe(204);
    await page.goto("/"); // a fresh conversation on 10 May 2025
    await openTab("My plans");
    await expect(plans().getByText(/No plans yet/)).toBeVisible();
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

  // --- CFO recommendations on a day with spare cash (31 Dec 2025: all CHF instalments are paid) ---

  test("with spare cash the CFO recommends a tax reserve and an investment deposit", async () => {
    expect((await page.request.post("/api/demo/reset")).status()).toBe(204);
    await page.goto("/");
    await page.getByLabel("Today").fill("2025-12-31");
    await expect(heroFigure(FIGURES.decemberBalance)).toBeVisible();
    await expect(page.getByText("Status: On track")).toBeVisible();
    // Nothing needs rescuing, so there is no "Options" tab and the CFO advice is the panel on screen.
    await expect(tab("Options")).toHaveCount(0);
    await expect(tab("CFO advice")).toHaveAttribute("aria-selected", "true");

    // The proposals and their figures come from code and are on screen before the model's wording arrives.
    // No CHF instalment is left, so the currency advisor stays silent.
    const section = cfoRecommendations();
    await expect(section.getByRole("heading", { level: 3 })).toHaveText(["Tax", "Investments"]);
    await expect(depositCard("Tax")).toHaveCount(1);
    await expect(depositCard("Tax")).toContainText(FIGURES.decemberTaxReserve);
    await expect(depositCard("Tax")).toContainText("On track");
    await expect(depositCard("Investments")).toHaveCount(1);
    await expect(depositCard("Investments")).toContainText(FIGURES.decemberInvestmentDeposit);
    // The assumptions behind a figure are under the card's "Details".
    const assumptions = depositCard("Tax").getByText("Assumptions", { exact: true });
    await expect(assumptions).toBeHidden();
    const proposalDetails = depositCard("Tax").getByText(/^Details: /); // followed by the title, the model's wording
    await proposalDetails.click();
    await expect(assumptions).toBeVisible();
    await proposalDetails.click();
    await expect(assumptions).toBeHidden();
    await expect(section.getByText("Tick what you want to decide on.")).toBeVisible();
    await expect(section.getByRole("button", { name: "Approve selected" })).toBeDisabled();

    await expect(section.getByRole("status", { name: "Loading the summary of the recommendations" })).toBeHidden({
      timeout: LLM_TIMEOUT_MS,
    });
    const badge = section.getByText(/^(AI summary|Standard summary \(AI unavailable\))$/);
    await expect(badge).toBeVisible({ timeout: LLM_TIMEOUT_MS });
    console.log(`[CFO summary] ${await badge.innerText()}`);
    await waitForExplanation();
    await captureKeyState("13-cfo-recommendations");
  });

  test("approving both deposits together is simulated and recorded per recommendation", async () => {
    const section = cfoRecommendations();
    await openTab("CFO advice");
    await depositCard("Tax").getByRole("checkbox").check();
    await depositCard("Investments").getByRole("checkbox").check();
    await expect(page.getByRole("list", { name: "Chart legend" })).toContainText("Forecast with recommendations T1 + I1");

    await section.getByRole("button", { name: "Approve selected" }).click();
    const dialog = page.getByRole("dialog", { name: "Approve 2 recommendations?" });
    await expect(dialog.getByText("SIMULATED", { exact: true })).toBeVisible();
    await expect(dialog).toContainText(FIGURES.decemberTaxReserve);
    await expect(dialog).toContainText(FIGURES.decemberInvestmentDeposit);
    await expect(dialog.getByRole("listitem").last()).toContainText("(simulated)");
    await capture("14-cfo-approval-dialog-desktop");

    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();
    await expect(dialog).toBeHidden();

    const banner = page.getByRole("status").filter({ hasText: "2 recommendations approved" });
    await expect(banner.getByRole("listitem").first()).toContainText("(simulated)");
    await expect(page.getByText("Status: On track")).toBeVisible();

    await openTab("My plans");
    const deposits = plans().getByRole("listitem").filter({ hasText: "Term deposit ·" });
    await expect(deposits).toHaveCount(2);
    await expect(deposits.first()).toContainText("From an approved recommendation");
    await openTab("History");
    await expectTabCount("History", 2);
    for (const id of ["T1", "I1"]) {
      const row = history().getByRole("row", { name: new RegExp(`Recommendation ${id}: `) });
      await expect(row).toHaveCount(1);
      await expect(row).toContainText("Approved");
      await expect(row).toContainText("SIMULATED");
    }
    // What was approved is part of the forecast now and is not proposed again.
    await openTab("CFO advice");
    await expect(section).toBeVisible();
    await expect(depositCard("Tax")).toHaveCount(0);
    await expect(depositCard("Investments")).toHaveCount(0);
    await captureKeyState("15-cfo-approved");
  });

  test("resetting the demo removes the deposits and their decisions", async () => {
    await page.getByRole("button", { name: "Reset demo" }).click();
    await page.getByRole("dialog", { name: "Reset the demo?" }).getByRole("button", { name: "Delete plans and decisions" }).click();

    await expect(page.getByText("Demo reset: all plans and decisions were deleted.")).toBeVisible();
    await openTab("My plans");
    await expect(plans().getByText(/No plans yet/)).toBeVisible();
    await openTab("History");
    await expect(history().getByText(/No decisions yet/)).toBeVisible();
    await openTab("CFO advice");
    await expect(depositCard("Tax")).toHaveCount(1);
    await expect(depositCard("Investments")).toHaveCount(1);
  });
});
