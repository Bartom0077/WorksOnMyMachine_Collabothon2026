import { expect, test } from "@playwright/test";
import { chatProposal, chatSaved, closureEvent, greenDashboard, redDashboard } from "../fixtures/data";
import { apiError, mockApi } from "../fixtures/mockApi";

test.describe("assistant chat", () => {
  test("confirming a recognised plan saves it and refreshes the forecast", async ({ page }) => {
    const api = await mockApi(page, {
      dashboard: greenDashboard,
      events: [],
      chatQueue: [chatProposal, chatSaved],
      afterChat: (response, state) => {
        if (response.events_changed) {
          state.dashboard = redDashboard;
          state.events = [closureEvent];
        }
      },
    });
    await page.goto("/");
    await expect(page.getByText("No action needed")).toBeVisible();

    const chat = page.getByRole("complementary", { name: "Assistant" });
    await chat.getByRole("button", { name: "We are closing for holidays from 1 to 21 June" }).click();

    const confirmation = chat.getByRole("group", { name: "Plan awaiting your confirmation" });
    await expect(confirmation).toContainText("Closure");
    await expect(confirmation).toContainText("1 Jun – 21 Jun 2025");
    await expect(confirmation).toContainText("Holiday closure");
    await expect(chat.getByText("Data used: propose planned event")).toBeVisible();
    // Nothing is saved before the owner confirms.
    await expect(page.getByText("No action needed")).toBeVisible();

    await confirmation.getByRole("button", { name: "Yes, include it" }).click();

    await expect(chat.getByText("Forecast updated with your plans.")).toBeVisible();
    await expect(chat.getByText("Added to your plans: Holiday closure")).toBeVisible();
    await expect(page.getByText("Status: Shortfall ahead")).toBeVisible();
    await expect(page.getByRole("article")).toHaveCount(4);
    await expect(page.getByRole("region", { name: "My plans" }).getByText("Holiday closure")).toBeVisible();

    expect(api.requests.chat).toHaveLength(2);
    expect(api.requests.chat[0]).toEqual({
      as_of_date: "2025-05-10",
      horizon_days: 60,
      buffer_eur: 3000,
      message: "We are closing for holidays from 1 to 21 June",
      conversation_id: null,
    });
    expect(api.requests.chat[1]).toMatchObject({
      message: "Yes, please include it in the forecast.",
      conversation_id: "conv-1",
    });
    // Buttons of an answered confirmation are no longer actionable.
    await expect(confirmation.getByRole("button", { name: "Yes, include it" })).toBeDisabled();
  });

  test("“Correct it” moves focus to the message box with a hint", async ({ page }) => {
    await mockApi(page, { chatQueue: [chatProposal] });
    await page.goto("/");

    const input = page.getByLabel("Message to the assistant");
    await input.fill("We are closing for holidays in June");
    await input.press("Enter");
    await page.getByRole("button", { name: "Correct it" }).click();

    await expect(input).toBeFocused();
    await expect(input).toHaveAttribute("placeholder", /Tell me what to change/);
  });

  test("shows an unavailable assistant inline and retries the same message", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");

    const chat = page.getByRole("complementary", { name: "Assistant" });
    await chat.getByRole("button", { name: "Why is my balance dropping?" }).click();

    const alert = chat.getByRole("alert");
    await expect(alert).toContainText("The assistant is unavailable right now");
    await expect(alert).toContainText("assistant_unavailable");

    api.state.chatQueue = [{ ...chatSaved, reply: "Fixed costs continue while revenue stops.", events_changed: false, saved_events: [] }];
    await alert.getByRole("button", { name: "Try again" }).click();

    await expect(chat.getByText("Fixed costs continue while revenue stops.")).toBeVisible();
    await expect(alert).toHaveCount(0);
    expect(api.requests.chat.map((request) => request.message)).toEqual([
      "Why is my balance dropping?",
      "Why is my balance dropping?",
    ]);
    // The retried message is not duplicated in the transcript.
    await expect(chat.getByText("You said: Why is my balance dropping?")).toHaveCount(1);
  });
});

test.describe("my plans", () => {
  test("lists plans with their source and removes one", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");

    const plans = page.getByRole("region", { name: "My plans" });
    await expect(plans.getByRole("listitem")).toHaveCount(2);
    await expect(plans).toContainText("Closure · 1 Jun – 21 Jun 2025");
    await expect(plans).toContainText("Planned expense · 10 Jun 2025 · €8,000.00");
    await expect(plans.getByText("From chat").first()).toBeVisible();

    await plans.getByText("What you said").first().click();
    await expect(plans.getByText("We are closing for holidays from 1 to 21 June")).toBeVisible();

    const dashboardCalls = api.requests.dashboard.length;
    await plans.getByRole("button", { name: "Remove plan: New pizza oven" }).click();
    await expect(plans.getByRole("listitem")).toHaveCount(1);
    expect(api.requests.deletedEventIds).toEqual([12]);
    await expect.poll(() => api.requests.dashboard.length).toBeGreaterThan(dashboardCalls);
  });

  test("shows an error when a plan cannot be removed", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/events/12", (route) =>
      route.fulfill({ status: 404, json: apiError(404, "event_not_found", "Planned event 12 does not exist.").body }),
    );
    await page.goto("/");

    await page.getByRole("button", { name: "Remove plan: New pizza oven" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "The plan was not removed" })).toContainText(
      "Planned event 12 does not exist.",
    );
  });
});
