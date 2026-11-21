import { type Page, expect, test } from "@playwright/test";
import {
  approvedDashboard,
  approvedDecision,
  chatProposal,
  chatRemovalKept,
  chatRemovalProposal,
  chatRemoved,
  chatSaved,
  closureEvent,
  greenDashboard,
  redDashboard,
} from "../fixtures/data";
import { assistantPanel, openAssistant } from "../fixtures/assistant";
import { apiError, mockApi } from "../fixtures/mockApi";

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

/** Opens the "My plans" tab and returns its panel content. */
async function openPlans(page: Page) {
  await openTab(page, "My plans");
  return page.getByRole("region", { name: "My plans" });
}

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
    await openAssistant(page);
    await expect(page.getByText("No action needed")).toBeVisible();
    await expect(workspaceTab(page, "Options")).toHaveCount(0);
    await expect(workspaceTab(page, "CFO advice")).toHaveAttribute("aria-selected", "true");
    await expect(workspaceTab(page, "My plans")).toHaveText(/^My plans\s*0$/);

    const chat = assistantPanel(page);
    await expect(chat.getByText("I ask before saving anything.")).toBeVisible();
    await expect(chat.getByText("Tell me a plan and I will show how it changes your forecast.")).toBeVisible();
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
    // The new shortfall brings the Options tab, and the owner had not picked a tab, so it opens.
    await expect(workspaceTab(page, "Options")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("article")).toHaveCount(4);
    await expect(workspaceTab(page, "My plans")).toHaveText(/^My plans\s*1$/);
    const plans = await openPlans(page);
    await expect(plans.getByText("Holiday closure")).toBeVisible();

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

  test("Enter while a reply is pending keeps the typed message", async ({ page }) => {
    const answer = { ...chatSaved, events_changed: false, saved_events: [] };
    const api = await mockApi(page, { chatQueue: [answer, answer], chatDelayMs: 1500 });
    await page.goto("/");
    await openAssistant(page);

    const input = page.getByLabel("Message to the assistant");
    await input.fill("first message");
    await input.press("Enter");
    await expect(input).toHaveValue("");

    await input.fill("follow-up typed while waiting");
    await input.press("Enter");
    await expect(input).toHaveValue("follow-up typed while waiting");
    await expect(page.getByText(/Waiting for the assistant’s reply/)).toBeVisible();
    expect(api.requests.chat).toHaveLength(1);

    // Once the reply is in, the kept draft can be sent.
    await expect(page.getByText(/Waiting for the assistant’s reply/)).toHaveCount(0);
    await input.press("Enter");
    await expect(input).toHaveValue("");
    await expect.poll(() => api.requests.chat.map((request) => request.message)).toEqual([
      "first message",
      "follow-up typed while waiting",
    ]);
  });

  test("a plan saved by a reply to an abandoned conversation still refreshes the forecast", async ({ page }) => {
    const api = await mockApi(page, {
      dashboard: greenDashboard,
      events: [],
      chatQueue: [chatSaved],
      chatDelayMs: 1500,
      afterChat: (_response, state) => {
        state.dashboard = redDashboard;
        state.events = [closureEvent];
      },
    });
    await page.goto("/");
    await openAssistant(page);
    await expect(page.getByText("No action needed")).toBeVisible();

    await page.getByLabel("Message to the assistant").fill("Yes, please include it");
    await page.getByLabel("Message to the assistant").press("Enter");
    // Changing "today" starts a new conversation while the reply that saves the plan is still on its way.
    await page.getByLabel("Today").fill("2025-05-11");
    await expect(page.getByText("“Today” is now 11 May 2025. Started a new conversation.")).toBeVisible();

    await expect(page.getByText("Status: Shortfall ahead")).toBeVisible();
    const plans = await openPlans(page);
    await expect(plans.getByText("Holiday closure")).toBeVisible();
    // The outdated reply itself is not shown in the new conversation.
    await expect(page.getByText("Added to your plans: Holiday closure")).toHaveCount(0);
    expect(api.requests.chat).toHaveLength(1);
  });

  test("“Correct it” moves focus to the message box with a hint", async ({ page }) => {
    await mockApi(page, { chatQueue: [chatProposal] });
    await page.goto("/");
    await openAssistant(page);

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
    await openAssistant(page);

    const chat = assistantPanel(page);
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

  test("confirming a removal takes the plan out of “My plans” and reloads the forecast", async ({ page }) => {
    const api = await mockApi(page, {
      chatQueue: [chatRemovalProposal, chatRemoved],
      afterChat: (response, state) => {
        if (response.events_changed) state.dashboard = greenDashboard;
      },
    });
    await page.goto("/");
    await openAssistant(page);
    const plans = await openPlans(page);
    await expect(plans.getByRole("listitem")).toHaveCount(2);
    await expect(page.getByText("Status: Shortfall ahead")).toBeVisible();

    const chat = assistantPanel(page);
    const input = page.getByLabel("Message to the assistant");
    await input.fill("Please remove the oven purchase");
    await input.press("Enter");

    const removal = chat.getByRole("group", { name: "Plan awaiting removal" });
    await expect(removal).toContainText("Remove this plan?");
    await expect(removal).toContainText("Planned expense");
    await expect(removal).toContainText("10 Jun 2025");
    await expect(removal).toContainText("€8,000.00");
    await expect(removal).toContainText("New pizza oven");
    // Nothing is removed before the owner confirms.
    await expect(plans.getByRole("listitem")).toHaveCount(2);
    const dashboardCalls = api.requests.dashboard.length;
    const recommendationCalls = api.requests.recommendation;

    // Keyboard operable: the answer can be given without a pointer.
    await removal.getByRole("button", { name: "Yes, remove it" }).focus();
    await page.keyboard.press("Enter");

    await expect(chat.getByText("Removed from your plans: New pizza oven")).toBeVisible();
    await expect(chat.getByText("Forecast updated with your plans.")).toBeVisible();
    await expect(plans.getByRole("listitem")).toHaveCount(1);
    await expect(plans.getByText("New pizza oven")).toHaveCount(0);
    await expect(workspaceTab(page, "My plans")).toHaveText(/^My plans\s*1$/);
    await expect(page.getByText("No action needed")).toBeVisible();
    // The tab the owner picked stays open although the Options tab went away with the shortfall.
    await expect(workspaceTab(page, "Options")).toHaveCount(0);
    await expect(workspaceTab(page, "My plans")).toHaveAttribute("aria-selected", "true");
    await expect.poll(() => api.requests.dashboard.length).toBeGreaterThan(dashboardCalls);
    await expect.poll(() => api.requests.recommendation).toBeGreaterThan(recommendationCalls);

    expect(api.requests.chat).toHaveLength(2);
    expect(api.requests.chat[1]).toMatchObject({
      message: "Yes, please remove it from my plans.",
      conversation_id: "conv-2",
    });
    // The plan was removed by the assistant, not through the "My plans" endpoint.
    expect(api.requests.deletedEventIds).toEqual([]);
    // Buttons of an answered removal card are no longer actionable.
    await expect(removal.getByRole("button", { name: "Yes, remove it" })).toBeDisabled();
    await expect(removal.getByRole("button", { name: "Keep it" })).toBeDisabled();
  });

  test("“Keep it” removes nothing", async ({ page }) => {
    const api = await mockApi(page, { chatQueue: [chatRemovalProposal, chatRemovalKept] });
    await page.goto("/");
    await openAssistant(page);
    const plans = await openPlans(page);
    await expect(plans.getByRole("listitem")).toHaveCount(2);

    const chat = assistantPanel(page);
    const input = page.getByLabel("Message to the assistant");
    await input.fill("Please remove the oven purchase");
    await input.press("Enter");
    const removal = chat.getByRole("group", { name: "Plan awaiting removal" });
    await expect(removal).toBeVisible();
    const dashboardCalls = api.requests.dashboard.length;

    await removal.getByRole("button", { name: "Keep it" }).click();

    await expect(chat.getByText("All right, the plan stays in your forecast.")).toBeVisible();
    expect(api.requests.chat[1]).toMatchObject({ message: "No, keep it.", conversation_id: "conv-2" });
    await expect(plans.getByRole("listitem")).toHaveCount(2);
    await expect(plans.getByText("New pizza oven")).toBeVisible();
    await expect(chat.getByText(/Removed from your plans/)).toHaveCount(0);
    await expect(chat.getByText("Forecast updated with your plans.")).toHaveCount(0);
    expect(api.requests.dashboard.length).toBe(dashboardCalls);
    await expect(removal.getByRole("button", { name: "Keep it" })).toBeDisabled();
  });

  test("a removal card whose plan was already removed by hand does no harm", async ({ page }) => {
    const stale = {
      ...chatRemovalKept,
      reply: "I could not find that plan. Your current plans: Holiday closure.",
      tool_calls: [{ name: "list_planned_events", ok: true }],
    };
    const api = await mockApi(page, { chatQueue: [chatRemovalProposal, stale] });
    await page.goto("/");
    await openAssistant(page);
    const plans = await openPlans(page);
    const chat = assistantPanel(page);
    const input = page.getByLabel("Message to the assistant");
    await input.fill("Please remove the oven purchase");
    await input.press("Enter");
    const removal = chat.getByRole("group", { name: "Plan awaiting removal" });
    await expect(removal).toBeVisible();

    // The owner removes the plan in "My plans" while the card is still open.
    await plans.getByRole("button", { name: "Remove plan: New pizza oven" }).click();
    await expect(plans.getByRole("listitem")).toHaveCount(1);

    await removal.getByRole("button", { name: "Yes, remove it" }).click();
    await expect(chat.getByText("I could not find that plan. Your current plans: Holiday closure.")).toBeVisible();
    await expect(plans.getByRole("listitem")).toHaveCount(1);
    await expect(chat.getByRole("alert")).toHaveCount(0);
    expect(api.requests.deletedEventIds).toEqual([12]);

    // Reset demo starts a new conversation: the old card is gone with the transcript.
    await page.getByRole("button", { name: "Reset demo" }).click();
    await page.getByRole("dialog", { name: "Reset the demo?" }).getByRole("button", { name: "Delete plans and decisions" }).click();
    await expect(chat.getByText("Demo reset: all plans and decisions were deleted.")).toBeVisible();
    await expect(removal).toHaveCount(0);
  });
});

test.describe("my plans", () => {
  test("lists plans with their source and removes one", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");

    const plans = await openPlans(page);
    await expect(plans.getByRole("listitem")).toHaveCount(2);
    await expect(plans).toContainText("Closure · 1 Jun – 21 Jun 2025");
    await expect(plans).toContainText("Planned expense · 10 Jun 2025 · €8,000.00");
    await expect(plans.getByText("From chat").first()).toBeVisible();

    // The section heading is kept for assistive technology; the tab names the panel on screen.
    await expect(plans.getByRole("heading", { level: 2, name: "My plans" })).toBeAttached();
    const utterance = plans.getByText("We are closing for holidays from 1 to 21 June");
    await expect(utterance).toBeHidden();
    await plans.getByText("What you said").first().click();
    await expect(utterance).toBeVisible();

    const dashboardCalls = api.requests.dashboard.length;
    await plans.getByRole("button", { name: "Remove plan: New pizza oven" }).click();
    await expect(plans.getByRole("listitem")).toHaveCount(1);
    await expect(workspaceTab(page, "My plans")).toHaveText(/^My plans\s*1$/);
    expect(api.requests.deletedEventIds).toEqual([12]);
    await expect.poll(() => api.requests.dashboard.length).toBeGreaterThan(dashboardCalls);
  });

  test("only one plan is removed at a time", async ({ page }) => {
    const api = await mockApi(page);
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/events/12", async (route) => {
      await held;
      await route.fallback();
    });
    await page.goto("/");

    const plans = await openPlans(page);
    await plans.getByRole("button", { name: "Remove plan: New pizza oven" }).click();
    await expect(plans.getByRole("button", { name: "Remove plan: New pizza oven" })).toHaveText("Removing…");
    await expect(plans.getByRole("button", { name: "Remove plan: Holiday closure" })).toBeDisabled();

    release();
    await expect(plans.getByRole("listitem")).toHaveCount(1);
    await expect(plans.getByRole("button", { name: "Remove plan: Holiday closure" })).toBeEnabled();
    expect(api.requests.deletedEventIds).toEqual([12]);
  });

  test("removing a measure from an approved option asks for confirmation first", async ({ page }) => {
    const api = await mockApi(page, {
      dashboard: approvedDashboard,
      events: approvedDashboard.events,
      decisions: [approvedDecision],
    });
    await page.goto("/");
    await openTab(page, "History");
    const decisionRow = page.getByRole("row", { name: /E: Postpone transfers/ });
    await expect(decisionRow).toContainText("Approved");
    await expect(decisionRow).not.toContainText("Withdrawn");

    const plans = await openPlans(page);
    await page.getByRole("button", { name: "Withdraw option: Credit line of €15,000" }).click();
    const dialog = page.getByRole("dialog", { name: "Withdraw this approved option?" });
    await expect(dialog).toContainText("was put in place by an option you approved");
    // The whole option is withdrawn, so its other measure is named too.
    await expect(dialog.getByRole("listitem")).toHaveText([/Transfer to Zurich/, "Credit line of €15,000"]);
    expect(api.requests.deletedEventIds).toEqual([]);

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(api.requests.deletedEventIds).toEqual([]);

    await page.getByRole("button", { name: "Withdraw option: Credit line of €15,000" }).click();
    await dialog.getByRole("button", { name: "Withdraw option" }).click();
    await expect(dialog).toBeHidden();
    expect(api.requests.deletedEventIds).toEqual([22]);
    await expect(plans.getByText("From an approved option")).toHaveCount(0);

    await openTab(page, "History");
    await expect(decisionRow).toContainText("Withdrawn");
    await expect(decisionRow).toContainText("Approved, then withdrawn on");
    await expect(decisionRow).toContainText("Reversed: no longer in the forecast");
  });

  test("shows an error when a plan cannot be removed", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/events/12", (route) =>
      route.fulfill({ status: 404, json: apiError(404, "event_not_found", "Planned event 12 does not exist.").body }),
    );
    await page.goto("/");

    const plans = await openPlans(page);
    await plans.getByRole("button", { name: "Remove plan: New pizza oven" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "The plan was not removed" })).toContainText(
      "Planned event 12 does not exist.",
    );
  });
});
