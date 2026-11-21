import { expect, test } from "@playwright/test";
import { approvedDashboard, approvedDecision, llmRecommendation, redDashboard, rejectedDecision } from "../fixtures/data";
import { apiError, mockApi } from "../fixtures/mockApi";

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

    const historyRow = page.getByRole("row", { name: /E: Postpone transfers/ });
    await expect(historyRow).toContainText("Approved");
    await expect(historyRow).toContainText("SIMULATED");
    await expect(historyRow).toContainText("-€22,097.60");
    await expect(historyRow).toContainText("-€10,097.60");
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

    await page.getByRole("button", { name: "Reject option B" }).click();
    const optionB = page.getByRole("article", { name: /Option B/ });
    await expect(optionB.getByText("Rejected", { exact: true })).toBeVisible();
    await expect(optionB.getByRole("button", { name: /Reject/ })).toHaveCount(0);
    expect(api.requests.decisions[0]).toMatchObject({ option_id: "B", status: "rejected" });
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
    await optionB.getByRole("button", { name: "Preview on chart" }).click();
    await expect(legend).toContainText("Forecast with option B");

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
    await expect(page.getByRole("row", { name: /E: Postpone transfers/ })).toBeVisible();

    await page.getByRole("button", { name: "Reset demo" }).click();
    const dialog = page.getByRole("dialog", { name: "Reset the demo?" });
    await expect(dialog).toContainText("all planned events and all recorded decisions");
    expect(api.requests.resets).toBe(0);

    await dialog.getByRole("button", { name: "Delete plans and decisions" }).click();
    await expect(dialog).toBeHidden();
    expect(api.requests.resets).toBe(1);
    await expect(page.getByText(/No plans yet/)).toBeVisible();
    await expect(page.getByText(/No decisions yet/)).toBeVisible();
    await expect(page.getByText("Demo reset: all plans and decisions were deleted.")).toBeVisible();
  });
});
