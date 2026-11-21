import AxeBuilder from "@axe-core/playwright";
import { type Locator, type Page, expect, test } from "@playwright/test";
import {
  SURPLUS_COMBINED_MIN_BALANCE,
  adviceDecision,
  depositEvent,
  fallbackAdviceRecommendation,
  noAdvice,
  noAdviceRecommendation,
  redAdvice,
  redDashboard,
  surplusAdvice,
  surplusAdviceRecommendation,
  surplusDashboard,
} from "../fixtures/data";
import { type MockApi, type MockState, apiError, mockApi } from "../fixtures/mockApi";
import type { AdviceProposal } from "../../src/types/api";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

/** The titles the language model gave the proposals of the surplus scenario. */
const TITLES = {
  T1: "Reserve the income tax now",
  T2: "Have your prepayments raised",
  F2: "Lock in the Zurich exchange rate",
  I1: "Let €21,000 earn interest",
};

/** The code titles of the two deposits; like the backend's, they name the amount in whole euros. */
const CODE_TITLES = {
  T1: "Tax reserve deposit of €5,214",
  I1: "Investment deposit of €21,000",
};

function proposal(id: string): AdviceProposal {
  const found = surplusAdvice.proposals.find((candidate) => candidate.id === id);
  if (found === undefined) throw new Error(`No proposal ${id} in the surplus fixture`);
  return found;
}

type TabName = "Options" | "CFO advice" | "My plans" | "History";

function tabList(page: Page): Locator {
  return page.getByRole("tablist", { name: "Decisions and plans" });
}

/** A workspace tab by its label; its accessible name also carries the count ("CFO advice 4"). */
function tab(page: Page, name: TabName): Locator {
  return tabList(page).getByRole("tab", { name: new RegExp(`^${name}`) });
}

/** Selects a workspace tab; only the panel of the selected tab is rendered. */
async function openTab(page: Page, name: TabName): Promise<void> {
  await tab(page, name).click();
  await expect(tab(page, name)).toHaveAttribute("aria-selected", "true");
}

function section(page: Page): Locator {
  return page.getByRole("region", { name: "CFO recommendations" });
}

/** The group of one advisor, by its heading. */
function group(page: Page, heading: string): Locator {
  return section(page).getByRole("group", { name: heading, exact: true });
}

function card(page: Page, title: string): Locator {
  return section(page).getByRole("group", { name: title, exact: true });
}

/** The "Details" disclosure of a proposal card. */
function details(page: Page, title: string): Locator {
  return card(page, title).locator("details");
}

function detailsSummary(page: Page, title: string): Locator {
  return details(page, title).locator("summary");
}

/** GREEN with a surplus and four proposals (T1, T2, F2, I1) worded by the language model. */
async function loadSurplus(page: Page, overrides: Partial<MockState> = {}): Promise<MockApi> {
  const api = await mockApi(page, {
    dashboard: surplusDashboard,
    events: [],
    advice: surplusAdvice,
    adviceRecommendation: surplusAdviceRecommendation,
    ...overrides,
  });
  await page.goto("/");
  await expect(section(page).getByRole("heading", { level: 2 })).toBeVisible();
  return api;
}

async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([]);
}

test.describe("CFO recommendations", () => {
  test("is the first tab when there is nothing to rescue and groups the proposals by advisor", async ({ page }) => {
    await loadSurplus(page);
    await expect(section(page).getByText("AI summary")).toBeVisible();

    // No rescue options: the "all clear" note sits above the tabs and the recommendations are the default tab.
    await expect(tabList(page).getByRole("tab")).toHaveText(["CFO advice 4", "My plans 0", "History 0"]);
    await expect(tab(page, "CFO advice")).toHaveAttribute("aria-selected", "true");
    const cardTitles = await page.getByRole("main").getByRole("heading", { level: 2 }).allTextContents();
    expect(cardTitles.slice(cardTitles.indexOf("Next steps")).map((title) => title.trim())).toEqual([
      "Next steps",
      "CFO recommendations",
    ]);
    await expect(page.getByRole("tabpanel")).toHaveCount(1);
    await expect(page.getByRole("tabpanel", { name: /^CFO advice/ })).toContainText("Cover the tax bill and put the rest to work");
    await expect(section(page).getByRole("heading", { level: 3 })).toHaveText([
      "Tax",
      "Currency (CHF transfers)",
      "Investments",
    ]);
    await expect(group(page, "Tax").getByRole("checkbox")).toHaveCount(2);
    await expect(group(page, "Currency (CHF transfers)").getByRole("checkbox")).toHaveCount(1);
    await expect(group(page, "Investments").getByRole("checkbox")).toHaveCount(1);

    await expect(section(page).getByText("Cover the tax bill and put the rest to work")).toBeVisible();
    // Only the opening sentence of the summary is shown; the rest is one click away.
    await expect(section(page).getByText("You have €27,000 more than you need.", { exact: true })).toBeVisible();
    const rest = section(page).getByText("Reserve the income tax first, then let the remaining cash earn interest.");
    await expect(rest).toBeHidden();
    await section(page).getByText("Read more").click();
    await expect(rest).toBeVisible();
  });

  test("the tabs show counts and work from the keyboard", async ({ page }) => {
    await loadSurplus(page, { events: [depositEvent(7, proposal("I1"))] });
    const advice = tab(page, "CFO advice");
    const plans = tab(page, "My plans");
    const history = tab(page, "History");
    await expect(tabList(page).getByRole("tab")).toHaveText(["CFO advice 4", "My plans 1", "History 0"]);

    // Roving tabindex: only the selected tab is in the tab order, and it names the panel it controls.
    await expect(advice).toHaveAttribute("tabindex", "0");
    await expect(plans).toHaveAttribute("tabindex", "-1");
    await expect(history).toHaveAttribute("tabindex", "-1");
    const advicePanelId = await advice.getAttribute("aria-controls");
    expect(advicePanelId).not.toBeNull();
    await expect(page.getByRole("tabpanel")).toHaveAttribute("id", advicePanelId ?? "");

    await advice.focus();
    await page.keyboard.press("ArrowRight");
    await expect(plans).toBeFocused();
    await expect(plans).toHaveAttribute("aria-selected", "true");
    await expect(plans).toHaveAttribute("tabindex", "0");
    await expect(advice).toHaveAttribute("aria-selected", "false");
    await expect(advice).toHaveAttribute("tabindex", "-1");
    await expect(page.getByRole("tabpanel")).toHaveCount(1);
    await expect(page.getByRole("tabpanel")).toHaveAttribute("id", (await plans.getAttribute("aria-controls")) ?? "");
    await expect(page.getByRole("region", { name: "My plans" }).getByRole("listitem")).toHaveCount(1);
    await expect(section(page)).toHaveCount(0);

    await page.keyboard.press("End");
    await expect(history).toBeFocused();
    await expect(history).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("No decisions yet. Approvals and rejections are recorded here.")).toBeVisible();

    // The arrows wrap around at both ends.
    await page.keyboard.press("ArrowRight");
    await expect(advice).toBeFocused();
    await expect(advice).toHaveAttribute("aria-selected", "true");
    await expect(section(page).getByRole("checkbox")).toHaveCount(4);
    await page.keyboard.press("ArrowLeft");
    await expect(history).toBeFocused();
    await expect(history).toHaveAttribute("aria-selected", "true");

    await page.keyboard.press("Home");
    await expect(advice).toBeFocused();
    await expect(advice).toHaveAttribute("aria-selected", "true");

    // Tab leaves the tab list for the panel, then its first control, instead of walking through the other tabs.
    await page.keyboard.press("Tab");
    await expect(page.getByRole("tabpanel")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(section(page).getByText("Read more")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(section(page).getByRole("checkbox").first()).toBeFocused();
  });

  test("a selection survives a visit to another tab", async ({ page }) => {
    await loadSurplus(page);
    await expect(section(page).getByText("AI summary")).toBeVisible();
    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    await detailsSummary(page, TITLES.T1).click();

    await openTab(page, "History");
    await expect(section(page)).toHaveCount(0);
    await openTab(page, "CFO advice");

    await expect(section(page).getByRole("checkbox", { name: TITLES.T1 })).toBeChecked();
    await expect(section(page).getByText("1 of 4 selected.")).toBeVisible();
    await expect(section(page).getByRole("button", { name: "Approve selected" })).toBeEnabled();
    // The panel is rendered anew, so its disclosures are collapsed again.
    await expect(details(page, TITLES.T1)).not.toHaveAttribute("open");
  });

  test("coming back to the tab after a decision does not move the focus into the panel", async ({ page }) => {
    await loadSurplus(page, {
      onAdviceDecision: (request) => request.proposal_ids.map((id, index) => adviceDecision(index + 1, proposal(id), "rejected")),
    });
    await expect(section(page).getByText("AI summary")).toBeVisible();
    await section(page).getByRole("checkbox", { name: TITLES.F2 }).check();
    await section(page).getByRole("button", { name: "Reject selected" }).click();
    await expect(section(page)).toBeFocused();
    await expect(tab(page, "History")).toHaveText("History 1");

    await tab(page, "CFO advice").focus();
    await page.keyboard.press("ArrowRight");
    await expect(tab(page, "My plans")).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(tab(page, "CFO advice")).toHaveAttribute("aria-selected", "true");
    await expect(section(page).getByRole("heading", { level: 2 })).toBeAttached();
    await expect(tab(page, "CFO advice")).toBeFocused();
  });

  test("a card shows the figures and effect of its proposal; dates and assumptions are under Details", async ({ page }) => {
    await loadSurplus(page);

    const reserve = card(page, TITLES.T1);
    await expect(reserve.getByText("Recommended")).toBeVisible();
    await expect(reserve.getByRole("term")).toHaveText(["Effect", "Amount", "Interest earned", "New lowest balance"]);
    await expect(reserve).toContainText("Amount€5,214.37");
    await expect(reserve).toContainText("Interest earned€8.86");
    await expect(reserve).toContainText("New lowest balance€25,025.93");
    await expect(reserve.getByText("On track")).toBeVisible();
    await expect(reserve).toContainText(proposal("T1").effect);

    // The rest is one click away: collapsed by default, opened by its summary.
    const reserveDetails = details(page, TITLES.T1);
    await expect(reserveDetails).not.toHaveAttribute("open");
    await expect(reserveDetails.getByText("Dates: 11 May – 11 Jun 2025")).toBeHidden();
    await expect(reserve.getByRole("list", { name: "Assumptions" })).toHaveCount(0);
    await expect(detailsSummary(page, TITLES.T1)).toHaveText(`Details: ${TITLES.T1}`);
    await detailsSummary(page, TITLES.T1).click();
    await expect(reserveDetails).toHaveAttribute("open");
    await expect(reserveDetails.getByText("Dates: 11 May – 11 Jun 2025")).toBeVisible();
    await expect(reserveDetails.getByText("Lowest balance on 15 May 2025.")).toBeVisible();
    await expect(reserve.getByRole("list", { name: "Assumptions" }).getByRole("listitem")).toHaveText([
      "Deposit rate of 2% a year (illustrative, not a bank quote).",
      "Deposit term of one month.",
    ]);
    // Opening one card leaves the others collapsed.
    await expect(details(page, TITLES.I1)).not.toHaveAttribute("open");

    // The disclosure works from the keyboard too.
    await detailsSummary(page, TITLES.T1).focus();
    await page.keyboard.press("Enter");
    await expect(reserveDetails).not.toHaveAttribute("open");
    await page.keyboard.press("Space");
    await expect(reserveDetails).toHaveAttribute("open");

    // A proposal that moves no money has no amount, no new balance and nothing to preview.
    const forward = card(page, TITLES.F2);
    await expect(forward).toContainText("Risk removed€400.00");
    await detailsSummary(page, TITLES.F2).click();
    await expect(details(page, TITLES.F2).getByText("Dates: Until 15 Dec 2025")).toBeVisible();
    await expect(details(page, TITLES.F2)).not.toContainText("Lowest balance on");
    await expect(forward).not.toContainText("Amount");
    await expect(forward).not.toContainText("New lowest balance");
    await expect(forward.getByText("Recommended")).toHaveCount(0);
    await expect(forward.getByRole("button", { name: /Preview on chart/ })).toHaveCount(0);

    const advice = card(page, TITLES.T2);
    await expect(advice).not.toContainText("Assumptions");
    await expect(advice.getByRole("checkbox", { name: TITLES.T2 })).toBeVisible();
  });

  test("shows the code texts first and replaces them with the AI wording when it arrives", async ({ page }) => {
    await loadSurplus(page, { adviceRecommendationDelayMs: 1500 });

    await expect(section(page).getByRole("status", { name: "Loading the summary of the recommendations" })).toBeVisible();
    const codeCard = card(page, CODE_TITLES.T1);
    await expect(codeCard).toContainText(proposal("T1").description);
    await expect(codeCard.getByText("Recommended")).toHaveCount(0);
    await expect(section(page).getByRole("checkbox")).toHaveCount(4);

    await expect(section(page).getByText("AI summary")).toBeVisible();
    await expect(codeCard).toHaveCount(0);
    await expect(card(page, TITLES.T1)).toContainText("Setting €5,214.37 aside today");
    await expect(card(page, TITLES.T1)).not.toContainText(proposal("T1").description);
  });

  test("a fallback answer shows the code texts without an error", async ({ page }) => {
    await loadSurplus(page, { adviceRecommendation: fallbackAdviceRecommendation(surplusAdvice) });

    await expect(section(page).getByText("Standard summary (AI unavailable)")).toBeVisible();
    await expect(section(page).getByText("Recommendations for your spare cash")).toBeVisible();
    await expect(card(page, CODE_TITLES.T1)).toContainText(proposal("T1").description);
    await expect(card(page, CODE_TITLES.I1)).toContainText(proposal("I1").description);
    await expect(section(page).getByRole("alert")).toHaveCount(0);
    await expect(section(page).getByText("AI summary")).toHaveCount(0);
  });

  test("a failing summary keeps the cards usable and can be retried", async ({ page }) => {
    const api = await loadSurplus(page, { adviceRecommendation: apiError(502, "bad_gateway", "Upstream unavailable.") });

    const alert = section(page).getByRole("alert").filter({ hasText: "The summary could not be loaded" });
    await expect(alert).toContainText("Upstream unavailable.");
    await card(page, CODE_TITLES.T1).getByRole("checkbox").check();
    await expect(section(page).getByRole("button", { name: "Approve selected" })).toBeEnabled();

    api.state.adviceRecommendation = surplusAdviceRecommendation;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(alert).toHaveCount(0);
    // The selection survives the new wording.
    await expect(card(page, TITLES.T1).getByRole("checkbox")).toBeChecked();
  });

  test("tips of an advisor are listed under its group and cannot be selected", async ({ page }) => {
    await loadSurplus(page);

    const tips = group(page, "Tax").getByRole("list", { name: "Tips" });
    await expect(tips.getByRole("listitem")).toHaveText([/^Tip\s*Keep the reserve separate\. Money in the everyday account/]);
    await expect(tips.getByRole("checkbox")).toHaveCount(0);
    await expect(section(page).getByRole("list", { name: "Tips" })).toHaveCount(1);
  });

  test("says so when there is nothing to recommend", async ({ page }) => {
    await loadSurplus(page, { advice: noAdvice, adviceRecommendation: noAdviceRecommendation });

    await expect(section(page).getByText("No further recommendations right now.")).toHaveCount(1);
    await expect(section(page).getByRole("checkbox")).toHaveCount(0);
    await expect(section(page).getByRole("button")).toHaveCount(0);
    await expect(section(page).getByRole("heading", { level: 3 })).toHaveCount(0);
    await expectNoAxeViolations(page);
  });

  test("an empty list still shows the advisors' tips", async ({ page }) => {
    const tip = { title: "Review this after the summer.", text: "Your surplus may be larger by then." };
    await loadSurplus(page, {
      advice: noAdvice,
      adviceRecommendation: {
        ...noAdviceRecommendation,
        agents: [{ agent: "investment", source: "llm", fallback_reason: null, tips: [tip] }],
      },
    });

    await expect(section(page).getByText("No further recommendations right now.")).toBeVisible();
    await expect(group(page, "Investments").getByRole("list", { name: "Tips" })).toContainText("Review this after the summer.");
  });

  test("the default RED scenario offers only the tax advice", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");

    // With rescue options on offer the recommendations wait behind the "Options" tab.
    await expect(tabList(page).getByRole("tab")).toHaveText(["Options 4", "CFO advice 1", "My plans 2", "History 0"]);
    await expect(tab(page, "Options")).toHaveAttribute("aria-selected", "true");
    await expect(section(page)).toHaveCount(0);
    await openTab(page, "CFO advice");
    await expect(page.getByRole("region", { name: "Your options" })).toHaveCount(0);

    await expect(section(page).getByRole("heading", { level: 3 })).toHaveText(["Tax"]);
    await expect(section(page).getByRole("checkbox")).toHaveCount(1);
    await expect(card(page, "Start a tax reserve once the cash gap is closed")).toContainText("About €2,340");
    await expect(section(page).getByRole("button", { name: /Preview on chart/ })).toHaveCount(0);
    expect(api.requests.advice[0]?.toString()).toBe("as_of_date=2025-05-10&horizon_days=60&buffer_eur=3000");
    expect(api.requests.adviceRecommendation[0]).toEqual({ as_of_date: "2025-05-10", horizon_days: 60, buffer_eur: 3000 });
  });

  test("shows a loading state, then an error that can be retried", async ({ page }) => {
    const api = await mockApi(page, {
      dashboard: surplusDashboard,
      events: [],
      advice: apiError(503, "database_unavailable", "The database is not reachable."),
      adviceDelayMs: 800,
      adviceRecommendation: surplusAdviceRecommendation,
    });
    await page.goto("/");

    await expect(section(page).getByRole("status", { name: "Loading the CFO recommendations" })).toBeVisible();
    // No count badge while the number of recommendations is unknown.
    await expect(tab(page, "CFO advice")).toHaveText("CFO advice");
    const alert = section(page).getByRole("alert").filter({ hasText: "The recommendations could not be loaded" });
    await expect(alert).toContainText("The database is not reachable.");
    await expect(alert).toContainText("database_unavailable");
    await expect(section(page).getByRole("checkbox")).toHaveCount(0);
    expect(api.requests.adviceRecommendation).toHaveLength(0);

    api.state.advice = surplusAdvice;
    api.state.adviceDelayMs = 0;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(alert).toHaveCount(0);
    await expect(section(page).getByRole("checkbox")).toHaveCount(4);
    await expect(tab(page, "CFO advice")).toHaveText("CFO advice 4");
  });

  test("several proposals can be selected; the actions need a selection", async ({ page }) => {
    await loadSurplus(page);
    await expect(section(page).getByText("AI summary")).toBeVisible();

    const approve = section(page).getByRole("button", { name: "Approve selected" });
    const reject = section(page).getByRole("button", { name: "Reject selected" });
    await expect(approve).toBeDisabled();
    await expect(reject).toBeDisabled();
    await expect(section(page).getByText("Tick what you want to decide on.")).toBeVisible();

    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    // The title is the checkbox's label: clicking it selects too.
    await card(page, TITLES.I1).getByText(TITLES.I1, { exact: true }).click();
    await expect(section(page).getByRole("checkbox", { name: TITLES.I1 })).toBeChecked();
    await expect(section(page).getByText("2 of 4 selected.")).toBeVisible();
    await expect(approve).toBeEnabled();
    await expect(reject).toBeEnabled();

    await section(page).getByRole("checkbox", { name: TITLES.T1 }).uncheck();
    await section(page).getByRole("checkbox", { name: TITLES.I1 }).uncheck();
    await expect(approve).toBeDisabled();
    await expect(reject).toBeDisabled();
  });

  test("the chart previews one proposal, or the selected ones together", async ({ page }) => {
    await loadSurplus(page);
    await expect(section(page).getByText("AI summary")).toBeVisible();
    const legend = page.getByRole("list", { name: "Chart legend" });
    await expect(legend).not.toContainText("Forecast with");
    // The chart's text summary and table sit in its disclosure.
    await page.getByText("Details and table", { exact: true }).click();

    const preview = card(page, TITLES.T1).getByRole("button", { name: /Preview on chart/ });
    await expect(preview).toHaveAttribute("aria-pressed", "false");
    await preview.click();
    const shown = card(page, TITLES.T1).getByRole("button", { name: /Shown on chart/ });
    await expect(shown).toHaveAttribute("aria-pressed", "true");
    await expect(legend.getByRole("listitem").filter({ hasText: "Forecast with" })).toHaveText("Forecast with recommendation T1");
    await expect(page.getByText(/With recommendation T1 \(.+\) the lowest point is €25,025.93 on 15 May 2025/)).toBeVisible();
    await expect(page.locator(".recharts-line")).toHaveCount(3);

    // Asking again takes the proposal off the chart.
    await shown.click();
    await expect(legend).not.toContainText("Forecast with");
    await expect(page.locator(".recharts-line")).toHaveCount(2);

    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    await expect(legend).toContainText("Forecast with recommendation T1");
    await section(page).getByRole("checkbox", { name: TITLES.I1 }).check();
    await expect(legend.getByRole("listitem").filter({ hasText: "Forecast with" })).toHaveText("Forecast with recommendations T1 + I1");
    await expect(page.getByText(/With recommendations T1 \+ I1 \(.+\) the lowest point is €4,025.93 on 15 May 2025/)).toBeVisible();
    expect(SURPLUS_COMBINED_MIN_BALANCE).toBe(4025.93);

    // Each day of the combined line is the baseline plus the effect of both deposits.
    await expect(page.getByRole("columnheader", { name: "With recommendations T1 + I1" })).toBeVisible();
    const lowestDay = page.getByRole("row", { name: /^15 May 2025/ });
    await expect(lowestDay).toContainText("€30,240.30");
    await expect(lowestDay).toContainText("€4,025.93");
    // After the tax reserve has matured only the investment deposit (and the reserve's interest) is missing.
    const lastDay = page.getByRole("row", { name: /^9 Jul 2025/ });
    await expect(lastDay).toContainText("€38,020.35");
    await expect(lastDay).toContainText("€17,029.21");

    // A proposal that moves no money leaves the combined line alone.
    await section(page).getByRole("checkbox", { name: TITLES.F2 }).check();
    await expect(legend).toContainText("Forecast with recommendations T1 + I1");

    // A single preview takes over from the selection until the selection changes again.
    await card(page, TITLES.I1).getByRole("button", { name: /Preview on chart/ }).click();
    await expect(legend.getByRole("listitem").filter({ hasText: "Forecast with" })).toHaveText("Forecast with recommendation I1");
    await section(page).getByRole("checkbox", { name: TITLES.T1 }).uncheck();
    await section(page).getByRole("checkbox", { name: TITLES.I1 }).uncheck();
    await expect(legend).not.toContainText("Forecast with");
  });

  test("a rescue option and a recommendation are never previewed together: the latest choice wins", async ({ page }) => {
    // Unrealistic on purpose: rescue options (RED) next to cash-blocking proposals, to exercise the hand-over.
    await mockApi(page, {
      advice: { ...surplusAdvice, proposals: surplusAdvice.proposals.map((entry) => ({ ...entry, forecast: redDashboard.forecast.days })) },
      adviceRecommendation: surplusAdviceRecommendation,
    });
    await page.goto("/");
    const legend = page.getByRole("list", { name: "Chart legend" });
    const optionC = page.getByRole("article", { name: /Option C/ });
    const optionE = page.getByRole("article", { name: /Option E/ });
    await expect(legend).toContainText("Forecast with option E");
    await expect(optionE.getByRole("button", { name: /Shown on chart/ })).toBeVisible();

    // The options and the recommendations live in different tabs; the chart above them is shared.
    await openTab(page, "CFO advice");
    await expect(section(page).getByText("AI summary")).toBeVisible();
    await expect(legend).toContainText("Forecast with option E");
    await section(page).getByRole("checkbox", { name: TITLES.I1 }).check();
    await expect(legend).toContainText("Forecast with recommendation I1");
    await expect(legend).not.toContainText("Forecast with option");
    await expect(page.locator(".recharts-line")).toHaveCount(3);
    await openTab(page, "Options");
    await expect(optionE.getByRole("button", { name: /Preview on chart/ })).toHaveAttribute("aria-pressed", "false");
    await expect(legend).toContainText("Forecast with recommendation I1");

    await optionC.getByRole("button", { name: /Preview on chart/ }).click();
    await expect(legend).toContainText("Forecast with option C");
    await expect(legend).not.toContainText("Forecast with recommendation");
    // The selection itself is kept; only the chart moved on.
    await openTab(page, "CFO advice");
    await expect(section(page).getByRole("checkbox", { name: TITLES.I1 })).toBeChecked();
    await expect(legend).toContainText("Forecast with option C");

    await card(page, TITLES.T1).getByRole("button", { name: /Preview on chart/ }).click();
    await expect(legend).toContainText("Forecast with recommendation T1");
    await openTab(page, "Options");
    await expect(optionC.getByRole("button", { name: /Preview on chart/ })).toHaveAttribute("aria-pressed", "false");
  });

  test("approving the selection asks for confirmation, posts it once and refreshes everything", async ({ page }) => {
    const approved = [proposal("T1"), proposal("I1")];
    const api = await loadSurplus(page, {
      onAdviceDecision: (_request, state) => {
        state.advice = { ...surplusAdvice, proposals: [proposal("T2"), proposal("F2")] };
        state.events = approved.map((entry, index) => depositEvent(index + 1, entry));
        return approved.map((entry, index) => adviceDecision(index + 1, entry, "approved"));
      },
    });
    await expect(section(page).getByText("AI summary")).toBeVisible();
    const requestsBefore = {
      dashboard: api.requests.dashboard.length,
      advice: api.requests.advice.length,
      recommendation: api.requests.adviceRecommendation.length,
    };

    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    await section(page).getByRole("checkbox", { name: TITLES.I1 }).check();
    await section(page).getByRole("button", { name: "Approve selected" }).click();

    const dialog = page.getByRole("dialog", { name: "Approve 2 recommendations?" });
    await expect(dialog.getByText("SIMULATED", { exact: true })).toBeVisible();
    await expect(dialog.getByText(/No real payment, transfer or deposit will be made/)).toBeVisible();
    await expect(dialog.getByRole("listitem")).toHaveText([
      `${TITLES.T1} Amount: €5,214.37`,
      `${TITLES.I1} Amount: €21,000.00`,
      "Term deposit of €5,214.37 opened with Commerzbank as a tax reserve (simulated).",
      "Term deposit of €21,000.00 opened with Commerzbank for six months (simulated).",
    ]);
    await expect(dialog).toContainText("Together, your lowest projected balance becomes €4,025.93 on 15 May 2025.");
    await expect(dialog).toContainText("What will be simulated");
    await expect(dialog).toContainText("recorded in your decision history");
    expect(api.requests.adviceDecisions).toHaveLength(0);

    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();
    await expect(dialog).toBeHidden();

    expect(api.requests.adviceDecisions).toEqual([
      {
        as_of_date: "2025-05-10",
        horizon_days: 60,
        buffer_eur: 3000,
        proposal_ids: ["T1", "I1"],
        status: "approved",
        explanations: {
          T1: surplusAdviceRecommendation.proposals.T1?.explanation,
          I1: surplusAdviceRecommendation.proposals.I1?.explanation,
        },
        fingerprints: { T1: "fp-t1-2", I1: "fp-i1-5" },
      },
    ]);

    const banner = page.getByRole("status").filter({ hasText: "2 recommendations approved" });
    await expect(banner.getByText("SIMULATED", { exact: true })).toBeVisible();
    await expect(banner.getByRole("listitem")).toHaveText(approved.flatMap((entry) => entry.simulated_actions));

    // Dashboard, recommendations (with their wording), plans and decisions are all requested again.
    await expect(section(page).getByRole("checkbox")).toHaveCount(2);
    await expect(section(page).getByRole("checkbox", { checked: true })).toHaveCount(0);
    await expect(section(page).getByRole("button", { name: "Approve selected" })).toBeDisabled();
    expect(api.requests.dashboard.length).toBe(requestsBefore.dashboard + 1);
    expect(api.requests.advice.length).toBe(requestsBefore.advice + 1);
    await expect.poll(() => api.requests.adviceRecommendation.length).toBe(requestsBefore.recommendation + 1);

    // The counts on the tabs follow: two recommendations left, two plans and two decisions more.
    await expect(tabList(page).getByRole("tab")).toHaveText(["CFO advice 2", "My plans 2", "History 2"]);

    await openTab(page, "My plans");
    const plans = page.getByRole("region", { name: "My plans" }).getByRole("listitem");
    await expect(plans).toHaveCount(2);
    await expect(plans.first()).toContainText("Term deposit · 11 May – 11 Jun 2025 · €5,214.37");
    await expect(plans.first()).toContainText("From an approved recommendation");

    await openTab(page, "History");
    const reserveRow = page.getByRole("row", { name: /Recommendation T1: Tax reserve deposit/ });
    await expect(reserveRow).toContainText("Approved");
    await expect(reserveRow).toContainText("SIMULATED");
    await expect(page.getByRole("row", { name: /Recommendation I1: Investment deposit/ })).toBeVisible();
    // The column is named for both kinds of decision it lists.
    await expect(page.getByRole("region", { name: "Decision history" }).first().getByRole("columnheader")).toContainText([
      "Option or recommendation",
    ]);

    await banner.getByRole("button", { name: "Dismiss message" }).click();
    await expect(banner).toHaveCount(0);
  });

  test("a single proposal is named in the confirmation; cancelling records nothing", async ({ page }) => {
    const api = await loadSurplus(page);
    await expect(section(page).getByText("AI summary")).toBeVisible();

    await section(page).getByRole("checkbox", { name: TITLES.T2 }).check();
    await section(page).getByRole("button", { name: "Approve selected" }).click();
    const dialog = page.getByRole("dialog", { name: `Approve recommendation T2: ${TITLES.T2}?` });
    // Advice moves no money: no amount and no new lowest balance.
    await expect(dialog.getByRole("listitem").first()).toHaveText(TITLES.T2);
    await expect(dialog).not.toContainText("lowest projected balance");
    await expect(dialog).toContainText("Request to raise the quarterly tax prepayments noted (simulated).");

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(api.requests.adviceDecisions).toHaveLength(0);
    await expect(section(page).getByRole("checkbox", { name: TITLES.T2 })).toBeChecked();
    // Nothing was settled: the focus goes back to the button that opened the confirmation.
    await expect(section(page).getByRole("button", { name: "Approve selected" })).toBeFocused();
    await expect(tab(page, "History")).toHaveText("History 0");
    await openTab(page, "History");
    await expect(page.getByText("No decisions yet. Approvals and rejections are recorded here.")).toBeVisible();
  });

  test("the confirmation states the exact amount apart from a code title that already names it", async ({ page }) => {
    await loadSurplus(page, { adviceRecommendation: fallbackAdviceRecommendation(surplusAdvice) });
    await expect(section(page).getByText("Standard summary (AI unavailable)")).toBeVisible();

    await card(page, CODE_TITLES.T1).getByRole("checkbox").check();
    await card(page, CODE_TITLES.I1).getByRole("checkbox").check();
    await section(page).getByRole("button", { name: "Approve selected" }).click();

    const approving = page.getByRole("dialog", { name: "Approve 2 recommendations?" }).getByRole("list").first();
    await expect(approving.getByRole("listitem")).toHaveText([
      `${CODE_TITLES.T1} Amount: €5,214.37`,
      `${CODE_TITLES.I1} Amount: €21,000.00`,
    ]);
    // The cents are a labelled value on a line of their own, never a second figure run on after the title.
    const exactAmount = approving.getByText("Amount: €5,214.37", { exact: true });
    await expect(exactAmount).toHaveCSS("display", "block");
    await expect(approving).not.toContainText(`${CODE_TITLES.T1}: €`);
  });

  test("rejecting the selection needs no confirmation and removes the proposals", async ({ page }) => {
    const api = await loadSurplus(page, {
      onAdviceDecision: (request, state) => {
        state.advice = {
          ...surplusAdvice,
          proposals: surplusAdvice.proposals.filter((entry) => !request.proposal_ids.includes(entry.id)),
        };
        return request.proposal_ids.map((id, index) => adviceDecision(index + 1, proposal(id), "rejected"));
      },
    });
    await expect(section(page).getByText("AI summary")).toBeVisible();

    await section(page).getByRole("checkbox", { name: TITLES.F2 }).check();
    await section(page).getByRole("button", { name: "Reject selected" }).click();

    await expect(section(page).getByRole("checkbox")).toHaveCount(3);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // The button that was used is disabled again; the focus stays in the section.
    await expect(section(page)).toBeFocused();
    expect(api.requests.adviceDecisions).toEqual([
      {
        as_of_date: "2025-05-10",
        horizon_days: 60,
        buffer_eur: 3000,
        proposal_ids: ["F2"],
        status: "rejected",
        explanations: { F2: surplusAdviceRecommendation.proposals.F2?.explanation },
        fingerprints: { F2: "fp-f2-4" },
      },
    ]);
    // Nothing was executed, so nothing is announced as simulated.
    await expect(page.getByRole("status").filter({ hasText: "approved" })).toHaveCount(0);
    await expect(tabList(page).getByRole("tab")).toHaveText(["CFO advice 3", "My plans 0", "History 1"]);
    await openTab(page, "History");
    const row = page.getByRole("row", { name: /Recommendation F2: Fix the exchange rate/ });
    await expect(row).toContainText("Rejected");
    await expect(row).toContainText("Not executed");
  });

  test("a proposal that is no longer available is reported and the recommendations are refreshed", async ({ page }) => {
    const api = await loadSurplus(page, {
      onAdviceDecision: (_request, state) => {
        state.advice = { ...surplusAdvice, proposals: [proposal("T2")] };
        return apiError(409, "proposal_not_available", "Recommendation I1 is not currently offered.");
      },
    });
    await expect(section(page).getByText("AI summary")).toBeVisible();
    const adviceRequestsBefore = api.requests.advice.length;

    await section(page).getByRole("checkbox", { name: TITLES.I1 }).check();
    await section(page).getByRole("button", { name: "Approve selected" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();

    await expect(dialog).toBeHidden();
    const alert = section(page).getByRole("alert").filter({ hasText: "Your decision was not saved" });
    await expect(alert).toContainText("Recommendation I1 is not currently offered.");
    await expect(alert).toContainText("proposal_not_available");
    await expect(section(page)).toBeFocused();
    await expect(section(page).getByRole("checkbox")).toHaveCount(1);
    await expect(section(page).getByRole("checkbox")).not.toBeChecked();
    expect(api.requests.advice.length).toBe(adviceRequestsBefore + 1);

    await alert.getByRole("button", { name: "Dismiss error" }).click();
    await expect(alert).toHaveCount(0);
  });

  test("another failure stays in the confirmation and keeps the selection", async ({ page }) => {
    await loadSurplus(page, {
      onAdviceDecision: () => apiError(503, "database_unavailable", "The database is not reachable."),
    });
    await expect(section(page).getByText("AI summary")).toBeVisible();

    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    await section(page).getByRole("button", { name: "Approve selected" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();

    await expect(dialog.getByRole("alert")).toContainText("The database is not reachable.");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(section(page).getByRole("checkbox", { name: TITLES.T1 })).toBeChecked();
    await expect(section(page).getByRole("alert")).toHaveCount(0);
  });

  test("decisions wait while the forecast is recalculated, and new parameters clear the selection", async ({ page }) => {
    const api = await loadSurplus(page);
    await expect(section(page).getByText("AI summary")).toBeVisible();
    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    const approve = section(page).getByRole("button", { name: "Approve selected" });
    await expect(approve).toBeEnabled();

    api.state.dashboardDelayMs = 1500;
    await page.getByRole("radio", { name: "90 days" }).check({ force: true });
    await expect(section(page).getByRole("checkbox", { checked: true })).toHaveCount(0);
    await expect.poll(() => api.requests.advice.at(-1)?.get("horizon_days")).toBe("90");

    // A new selection can be made right away, but it can only be decided on once the forecast is current.
    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    await expect(approve).toBeDisabled();
    await expect(section(page).getByRole("button", { name: "Reject selected" })).toBeDisabled();
    await expect(approve).toBeEnabled();
  });

  test("a selection is dropped when one of its proposals is no longer offered", async ({ page }) => {
    const api = await loadSurplus(page, { events: [depositEvent(7, proposal("I1"))] });
    await expect(section(page).getByText("AI summary")).toBeVisible();
    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    await section(page).getByRole("checkbox", { name: TITLES.I1 }).check();

    // Removing a plan recalculates everything; the investment deposit is then no longer on offer.
    api.state.advice = { ...surplusAdvice, proposals: redAdvice.proposals.concat(proposal("T1")) };
    await openTab(page, "My plans");
    await page.getByRole("button", { name: `Withdraw recommendation: ${CODE_TITLES.I1}` }).click();
    await page.getByRole("dialog", { name: "Withdraw this approved recommendation?" }).getByRole("button", { name: "Withdraw recommendation" }).click();
    await expect(tabList(page).getByRole("tab")).toHaveText(["CFO advice 2", "My plans 0", "History 0"]);
    // The owner was working in the plans: the tab stays where it is.
    await expect(tab(page, "My plans")).toHaveAttribute("aria-selected", "true");

    await openTab(page, "CFO advice");
    await expect(section(page).getByRole("checkbox")).toHaveCount(2);
    await expect(section(page).getByRole("checkbox", { checked: true })).toHaveCount(0);
    await expect(section(page).getByRole("button", { name: "Approve selected" })).toBeDisabled();
    // Nothing was settled in the confirmation: the focus is not pulled into this section.
    await expect(section(page)).not.toBeFocused();
  });

  test("everything works from the keyboard", async ({ page }) => {
    const api = await loadSurplus(page, {
      onAdviceDecision: (request) => request.proposal_ids.map((id, index) => adviceDecision(index + 1, proposal(id), "approved")),
    });
    await expect(section(page).getByText("AI summary")).toBeVisible();
    const legend = page.getByRole("list", { name: "Chart legend" });

    const checkbox = section(page).getByRole("checkbox", { name: TITLES.T1 });
    await checkbox.focus();
    await page.keyboard.press("Space");
    await expect(checkbox).toBeChecked();
    await expect(legend).toContainText("Forecast with recommendation T1");

    // The preview button follows the checkbox in the tab order.
    await page.keyboard.press("Tab");
    await expect(card(page, TITLES.T1).getByRole("button", { name: /Preview on chart/ })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(card(page, TITLES.T1).getByRole("button", { name: /Shown on chart/ })).toHaveAttribute("aria-pressed", "true");

    await section(page).getByRole("button", { name: "Approve selected" }).focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: /Approve recommendation T1/ });
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    expect(api.requests.adviceDecisions).toHaveLength(0);

    await page.keyboard.press("Enter");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Approve (simulated)" }).focus();
    await page.keyboard.press("Enter");
    await expect(dialog).toBeHidden();
    expect(api.requests.adviceDecisions[0]).toMatchObject({ proposal_ids: ["T1"], status: "approved" });
    await expect(page.getByRole("status").filter({ hasText: `Recommendation T1 approved: ${TITLES.T1}` })).toBeVisible();

    // "Approve selected" is disabled again, so the focus moves to the section heading instead of the page top.
    await expect(section(page).getByRole("button", { name: "Approve selected" })).toBeDisabled();
    await expect(section(page)).toBeFocused();
  });

  test("has no WCAG A/AA violations with a selection, in the confirmation and after the approval", async ({ page }) => {
    await loadSurplus(page, {
      onAdviceDecision: (request) => request.proposal_ids.map((id, index) => adviceDecision(index + 1, proposal(id), "approved")),
    });
    await expect(section(page).getByText("AI summary")).toBeVisible();
    await expectNoAxeViolations(page);

    await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
    await section(page).getByRole("checkbox", { name: TITLES.I1 }).check();
    await expectNoAxeViolations(page);

    await detailsSummary(page, TITLES.T1).click();
    await expect(details(page, TITLES.T1).getByText("Assumptions")).toBeVisible();
    await expectNoAxeViolations(page);

    await section(page).getByRole("button", { name: "Approve selected" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expectNoAxeViolations(page);

    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();
    await expect(page.getByRole("status").filter({ hasText: "2 recommendations approved" })).toBeVisible();
    await expectNoAxeViolations(page);

    // The decisions just recorded, in the tab they land in.
    await openTab(page, "History");
    await expect(page.getByRole("row", { name: /Recommendation T1/ })).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test.describe("mobile (375 px)", () => {
    test.use({ viewport: { width: 375, height: 812 } });

    test("fits the phone without horizontal scroll and keeps 24 px hit areas", async ({ page }) => {
      await loadSurplus(page);
      await expect(section(page).getByText("AI summary")).toBeVisible();
      await section(page).getByRole("checkbox", { name: TITLES.T1 }).check();
      await detailsSummary(page, TITLES.T1).click();
      await expect(details(page, TITLES.T1).getByText("Assumptions")).toBeVisible();
      // All tabs stay in view on the phone.
      for (const name of ["CFO advice", "My plans", "History"] as const) {
        await expect(tab(page, name)).toBeInViewport();
      }

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      const targets = [
        section(page).getByRole("checkbox").first(),
        card(page, TITLES.T1).getByText(TITLES.T1, { exact: true }),
        tab(page, "CFO advice"),
        tab(page, "History"),
      ];
      for (const target of targets) {
        const box = await target.boundingBox();
        expect(box?.width).toBeGreaterThanOrEqual(24);
        expect(box?.height).toBeGreaterThanOrEqual(24);
      }
      await expectNoAxeViolations(page);
    });
  });
});
