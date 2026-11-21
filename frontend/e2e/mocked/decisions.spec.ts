import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";
import { approvedDashboard, approvedDecision, llmRecommendation, redDashboard, rejectedDecision } from "../fixtures/data";
import { apiError, mockApi } from "../fixtures/mockApi";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

type TabName = "Options" | "CFO advice" | "My plans" | "History";

function workspaceTab(page: Page, name: TabName) {
  return page.getByRole("tablist", { name: "Decisions and plans" }).getByRole("tab", { name });
}

/** Selects a workspace tab; only the panel of the selected tab is rendered. */
async function openTab(page: Page, name: TabName) {
  const tab = workspaceTab(page, name);
  await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
}

/** Opens the "Details" disclosure of an option card, which holds the description and the reject action. */
async function openOptionDetails(page: Page, optionId: string) {
  await page.getByText(`Details of option ${optionId}`, { exact: true }).click();
}

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([]);
}

test.describe("workspace tabs", () => {
  test("opens on Options and shows a count on every tab", async ({ page }) => {
    await mockApi(page, { decisions: [approvedDecision] });
    await page.goto("/");

    const tabs = page.getByRole("tablist", { name: "Decisions and plans" }).getByRole("tab");
    await expect(tabs).toHaveText([/^Options\s*4$/, /^CFO advice\s*1$/, /^My plans\s*2$/, /^History\s*1$/]);
    await expect(workspaceTab(page, "Options")).toHaveAttribute("aria-selected", "true");
    await expect(tabs.and(page.locator('[aria-selected="true"]'))).toHaveCount(1);

    // Only the selected panel is in the DOM, and it is named by its tab.
    await expect(page.getByRole("tabpanel")).toHaveCount(1);
    await expect(page.getByRole("tabpanel", { name: /Options/ })).toBeVisible();
    await expect(page.getByRole("region", { name: "Your options" })).toBeVisible();
    await expect(page.getByRole("region", { name: "My plans" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Decision history" })).toHaveCount(0);
  });

  test("an empty tab shows a zero count", async ({ page }) => {
    await mockApi(page, { events: [] });
    await page.goto("/");

    await expect(workspaceTab(page, "My plans")).toHaveText(/^My plans\s*0$/);
    await expect(workspaceTab(page, "History")).toHaveText(/^History\s*0$/);
  });

  test("clicking a tab swaps the panel", async ({ page }) => {
    await mockApi(page, { decisions: [approvedDecision] });
    await page.goto("/");

    await openTab(page, "CFO advice");
    await expect(page.getByRole("tabpanel", { name: /CFO advice/ })).toBeVisible();
    await expect(page.getByRole("region", { name: "CFO recommendations" })).toBeVisible();
    await expect(page.getByRole("article", { name: /Option E/ })).toHaveCount(0);

    await openTab(page, "My plans");
    await expect(page.getByRole("region", { name: "My plans" }).getByRole("listitem")).toHaveCount(2);

    await openTab(page, "History");
    await expect(page.getByRole("row", { name: /E: Postpone transfers/ })).toBeVisible();
    await expect(page.getByRole("tabpanel")).toHaveCount(1);

    await openTab(page, "Options");
    await expect(page.getByRole("article")).toHaveCount(4);
  });

  test("arrow, Home and End keys move focus and selection along the tab list", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    const options = workspaceTab(page, "Options");
    const advice = workspaceTab(page, "CFO advice");
    const plans = workspaceTab(page, "My plans");
    const history = workspaceTab(page, "History");
    await expect(options).toHaveAttribute("aria-selected", "true");

    // Roving tabindex: only the selected tab is in the Tab order.
    await expect(options).toHaveAttribute("tabindex", "0");
    for (const tab of [advice, plans, history]) await expect(tab).toHaveAttribute("tabindex", "-1");

    await options.focus();
    await page.keyboard.press("ArrowRight");
    await expect(advice).toBeFocused();
    await expect(advice).toHaveAttribute("aria-selected", "true");
    await expect(advice).toHaveAttribute("tabindex", "0");
    await expect(options).toHaveAttribute("aria-selected", "false");
    await expect(options).toHaveAttribute("tabindex", "-1");
    await expect(page.getByRole("region", { name: "CFO recommendations" })).toBeVisible();

    await page.keyboard.press("End");
    await expect(history).toBeFocused();
    await expect(history).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText(/No decisions yet/)).toBeVisible();

    // Right on the last tab wraps to the first, Left on the first wraps to the last.
    await page.keyboard.press("ArrowRight");
    await expect(options).toBeFocused();
    await expect(options).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("ArrowLeft");
    await expect(history).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(plans).toBeFocused();
    await expect(plans).toHaveAttribute("aria-selected", "true");

    await page.keyboard.press("Home");
    await expect(options).toBeFocused();
    await expect(options).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("article")).toHaveCount(4);
  });

  test("the selected tab controls the panel on screen", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    for (const name of ["Options", "CFO advice", "My plans", "History"] as const) {
      await openTab(page, name);
      const panel = page.getByRole("tabpanel");
      const panelId = await panel.getAttribute("id");
      expect(panelId).not.toBeNull();
      await expect(workspaceTab(page, name)).toHaveAttribute("aria-controls", panelId ?? "");
    }
  });

  test("without options the workspace opens on CFO advice", async ({ page }) => {
    await mockApi(page, { dashboard: approvedDashboard, events: approvedDashboard.events });
    await page.goto("/");

    await expect(page.getByText("No action needed")).toBeVisible();
    await expect(workspaceTab(page, "Options")).toHaveCount(0);
    await expect(workspaceTab(page, "CFO advice")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("region", { name: "CFO recommendations" })).toBeVisible();
  });

  test("the plans, history and option details views have no WCAG A/AA violations", async ({ page }) => {
    await mockApi(page, { decisions: [approvedDecision, rejectedDecision] });
    await page.goto("/");
    await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();

    await openOptionDetails(page, "E");
    await expect(page.getByRole("button", { name: "Reject option E" })).toBeVisible();
    await expectNoAxeViolations(page);

    await openTab(page, "My plans");
    await expect(page.getByRole("region", { name: "My plans" }).getByRole("listitem")).toHaveCount(2);
    await expectNoAxeViolations(page);

    await openTab(page, "History");
    await expect(page.getByRole("row", { name: /E: Postpone transfers/ })).toBeVisible();
    await expectNoAxeViolations(page);
  });
});

test.describe("option cards", () => {
  test("the recommended option comes first with its reason and the primary approve button", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    const cards = page.getByRole("region", { name: "Your options" }).getByRole("article");
    await expect(cards).toHaveCount(4);
    await expect(cards.getByRole("heading", { level: 3 })).toHaveText([/^Option E: /, /^Option A: /, /^Option B: /, /^Option C: /]);

    const featured = cards.first();
    await expect(featured.getByText("Recommended", { exact: true })).toBeVisible();
    await expect(featured).toContainText(`Why option E: ${llmRecommendation.rationale}`);
    await expect(featured.getByRole("button", { name: "Approve option E" })).toHaveClass(/btn--primary/);
    await expect(page.getByText("Recommended", { exact: true })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Approve option A" })).not.toHaveClass(/btn--primary/);

    // At a glance: the new lowest balance with its status and the cost, nothing else.
    await expect(featured.getByRole("term")).toHaveText(["New lowest balance", "Cost"]);
    await expect(featured.getByRole("definition")).toHaveText([
      /^-€10,097\.60\s*On track\s*within the €15,000 credit line$/,
      "€61.85",
    ]);
    // The hint appears only where a credit line absorbs a negative balance.
    await expect(page.getByRole("article", { name: /Option B/ }).getByRole("definition").first()).toHaveText(
      /^-€10,097\.60\s*Shortfall ahead$/,
    );
  });

  test("details are collapsed by default and hold the description, costs and the reject action", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    const optionE = page.getByRole("article", { name: /Option E/ });
    const description = optionE.getByText("Postpone the transfers to Zurich and cover the rest with a €15,000 credit line.");
    await expect(optionE.getByRole("button", { name: "Approve option E" })).toBeVisible();
    await expect(description).toBeHidden();
    await expect(optionE.getByRole("button", { name: "Reject option E" })).toBeHidden();
    await expect(optionE.getByText("Cheapest way to stay safe.")).toBeHidden();

    await openOptionDetails(page, "E");
    await expect(description).toBeVisible();
    await expect(optionE.getByText("Lowest balance on 3 Jul 2025.")).toBeVisible();
    await expect(optionE.getByText("Cost: interest at 9% p.a. on the amount used (assumption)")).toBeVisible();
    await expect(optionE.getByText("Other cost: Requires a conversation with your brother")).toBeVisible();
    await expect(optionE.getByText("Cheapest way to stay safe.")).toBeVisible();
    await expect(optionE.getByRole("button", { name: "Reject option E" })).toBeVisible();

    // The other cards stay collapsed.
    await expect(page.getByRole("button", { name: "Reject option A" })).toBeHidden();

    // The disclosure is keyboard operable.
    const summary = page.getByText("Details of option E", { exact: true });
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(description).toBeHidden();
  });
});

test.describe("decisions", () => {
  test("approving an option asks for confirmation, then turns the outlook green", async ({ page }) => {
    const api = await mockApi(page, {
      onDecision: (_request, state) => {
        state.dashboard = approvedDashboard;
        state.events = approvedDashboard.events;
        return approvedDecision;
      },
    });
    await page.goto("/");
    await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Approve option E" }).click();
    const dialog = page.getByRole("dialog", { name: /Approve option E/ });
    await expect(dialog.getByText("SIMULATED", { exact: true })).toBeVisible();
    await expect(dialog.getByText(/No real payment, transfer or credit request/)).toBeVisible();
    await expect(dialog.getByText("Credit line request for €15,000 sent to Commerzbank (simulated).")).toBeVisible();
    expect(api.requests.decisions).toHaveLength(0);

    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();
    await expect(dialog).toBeHidden();

    expect(api.requests.decisions).toEqual([
      {
        as_of_date: "2025-05-10",
        horizon_days: 60,
        buffer_eur: 3000,
        option_id: "E",
        status: "approved",
        llm_explanation: llmRecommendation.explanation,
      },
    ]);

    const banner = page.getByRole("status").filter({ hasText: "Option E approved" });
    await expect(banner).toContainText("Transfers to Zurich on 15 May and 15 June postponed by two months (simulated).");
    await expect(page.getByRole("heading", { name: /You are covered|on track/ })).toBeVisible();
    await expect(page.getByText("No action needed")).toBeVisible();

    // With nothing left to decide the Options tab is gone and the workspace falls back to CFO advice.
    await expect(workspaceTab(page, "Options")).toHaveCount(0);
    await expect(workspaceTab(page, "CFO advice")).toHaveAttribute("aria-selected", "true");
    await expect(workspaceTab(page, "History")).toHaveText(/^History\s*1$/);

    await openTab(page, "History");
    const historyRow = page.getByRole("row", { name: /E: Postpone transfers/ });
    await expect(historyRow).toContainText("Approved");
    await expect(historyRow).toContainText("SIMULATED");
    await expect(historyRow).toContainText("“Today” was 10 May 2025");
    await expect(historyRow).toContainText("-€22,097.60");
    await expect(historyRow).toContainText("-€10,097.60");

    await openTab(page, "My plans");
    await expect(page.getByText("Credit line of €15,000")).toBeVisible();
  });

  test("cancelling the confirmation records nothing", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");

    await page.getByRole("button", { name: "Approve option A" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(api.requests.decisions).toHaveLength(0);
  });

  test("rejecting an option marks it as rejected", async ({ page }) => {
    const api = await mockApi(page, {
      onDecision: (_request, state) => {
        state.dashboard = {
          ...redDashboard,
          options: redDashboard.options.map((option) => (option.id === "B" ? { ...option, rejected: true } : option)),
        };
        return rejectedDecision;
      },
    });
    await page.goto("/");

    await openOptionDetails(page, "B");
    await page.getByRole("button", { name: "Reject option B" }).click();
    const optionB = page.getByRole("article", { name: /Option B/ });
    await expect(optionB.getByText("Rejected", { exact: true })).toBeVisible();
    await expect(optionB.getByRole("button", { name: /Reject/ })).toHaveCount(0);
    expect(api.requests.decisions[0]).toMatchObject({ option_id: "B", status: "rejected" });

    await openTab(page, "History");
    const historyRow = page.getByRole("row", { name: /B: Postpone the transfers/ });
    await expect(historyRow).toContainText("Rejected");
    // Nothing is executed for a rejection, so it is not labelled as a simulated execution.
    await expect(historyRow).toContainText("Not executed");
    await expect(historyRow).not.toContainText("SIMULATED");
  });

  test("rejecting the option being previewed returns the chart to the recommended one", async ({ page }) => {
    await mockApi(page, {
      onDecision: (_request, state) => {
        state.dashboard = {
          ...redDashboard,
          options: redDashboard.options.map((option) => (option.id === "B" ? { ...option, rejected: true } : option)),
        };
        return rejectedDecision;
      },
    });
    await page.goto("/");

    const legend = page.getByRole("list", { name: "Chart legend" });
    const optionB = page.getByRole("article", { name: /Option B/ });
    // The option id is a visually hidden suffix, which the accessible name separates with a space.
    await optionB.getByRole("button", { name: /^Preview on chart\s*: option B$/ }).click();
    await expect(optionB.getByRole("button", { name: /^Shown on chart\s*: option B$/ })).toHaveAttribute("aria-pressed", "true");
    await expect(legend).toContainText("Forecast with option B");

    await openOptionDetails(page, "B");
    await optionB.getByRole("button", { name: "Reject option B" }).click();
    await expect(optionB.getByText("Rejected", { exact: true })).toBeVisible();
    await expect(legend).toContainText("Forecast with option E");
  });

  test("a decision is taken on the forecast on screen, never on one that is still loading", async ({ page }) => {
    const api = await mockApi(page, {
      onDecision: (_request, state) => {
        state.dashboard = approvedDashboard;
        return approvedDecision;
      },
    });
    await page.goto("/");
    await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();

    // Open the confirmation for the 60-day board, then let a slow 90-day recalculation start behind it.
    await page.getByRole("button", { name: "Approve option E" }).click();
    const dialog = page.getByRole("dialog", { name: /Approve option E/ });
    api.state.dashboardDelayMs = 1500;
    api.state.dashboard = { ...redDashboard, horizon_days: 90 };
    await page.getByRole("radio", { name: "90 days" }).dispatchEvent("click");

    await expect(dialog.getByRole("button", { name: "Recalculating…" })).toBeDisabled();
    expect(api.requests.decisions).toHaveLength(0);

    // Once the new board is on screen the confirmation refers to it, and so does the request.
    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();
    await expect(dialog).toBeHidden();
    expect(api.requests.decisions[0]).toMatchObject({ horizon_days: 90, option_id: "E", status: "approved" });
  });

  test("option actions are disabled while the forecast is recalculated", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("article")).toHaveCount(4);
    await openOptionDetails(page, "A");

    api.state.dashboardDelayMs = 1500;
    await page.getByRole("radio", { name: "90 days" }).check({ force: true });
    await expect(page.getByRole("button", { name: "Approve option A" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Reject option A" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Approve option A" })).toBeEnabled();
  });

  test("shows the conflict when the option is no longer available", async ({ page }) => {
    await mockApi(page, {
      onDecision: () => apiError(409, "option_not_available", "Option A is not currently offered."),
    });
    await page.goto("/");

    await page.getByRole("button", { name: "Approve option A" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Option A is not currently offered.");
    await expect(dialog.getByRole("alert")).toContainText("option_not_available");
  });

  test("a conflict reloads the forecast so the unavailable option disappears", async ({ page }) => {
    await mockApi(page, {
      onDecision: (_request, state) => {
        state.dashboard = approvedDashboard;
        return apiError(409, "option_not_available", "Option A is not currently offered.");
      },
    });
    await page.goto("/");

    await page.getByRole("button", { name: "Approve option A" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Approve (simulated)" }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByRole("alert").filter({ hasText: "Your decision was not saved" })).toContainText(
      "Option A is not currently offered.",
    );
    await expect(page.getByRole("button", { name: "Approve option A" })).toHaveCount(0);
    await expect(page.getByText("No action needed")).toBeVisible();
  });

  test("resetting the demo needs confirmation and clears plans and decisions", async ({ page }) => {
    const api = await mockApi(page, { decisions: [approvedDecision] });
    await page.goto("/");
    await openTab(page, "History");
    await expect(page.getByRole("row", { name: /E: Postpone transfers/ })).toBeVisible();

    await page.getByRole("button", { name: "Reset demo" }).click();
    const dialog = page.getByRole("dialog", { name: "Reset the demo?" });
    await expect(dialog).toContainText("all planned events and all recorded decisions");
    expect(api.requests.resets).toBe(0);

    await dialog.getByRole("button", { name: "Delete plans and decisions" }).click();
    await expect(dialog).toBeHidden();
    expect(api.requests.resets).toBe(1);
    await expect(page.getByText("Demo reset: all plans and decisions were deleted.")).toBeVisible();
    await expect(page.getByText("No decisions yet. Approvals and rejections are recorded here.")).toBeVisible();
    await expect(workspaceTab(page, "History")).toHaveText(/^History\s*0$/);
    await expect(workspaceTab(page, "My plans")).toHaveText(/^My plans\s*0$/);
    await openTab(page, "My plans");
    await expect(page.getByText("No plans yet. Tell the assistant what is coming up.")).toBeVisible();
  });
});
