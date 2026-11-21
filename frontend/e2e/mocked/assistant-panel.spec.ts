import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";
import { assistantPanel, assistantToggle, openAssistant } from "../fixtures/assistant";
import { chatProposal, chatRemovalProposal, chatSaved } from "../fixtures/data";
import { mockApi } from "../fixtures/mockApi";

const STORAGE_KEY = "autonomous-cfo.assistant-open";
const PROMPT = "We are closing for holidays from 1 to 21 June";

function messageBox(page: Page) {
  return page.getByLabel("Message to the assistant");
}

function closeButton(page: Page) {
  return assistantPanel(page).getByRole("button", { name: "Close assistant" });
}

async function mainWidth(page: Page): Promise<number> {
  const box = await page.getByRole("main").boundingBox();
  return box?.width ?? 0;
}

async function loadDashboard(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("article")).toHaveCount(4);
}

test.describe("assistant panel", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("starts closed and the toggle docks it beside the forecast", async ({ page }) => {
    await mockApi(page);
    await loadDashboard(page);

    const toggle = assistantToggle(page);
    const panel = assistantPanel(page);
    await expect(panel).toBeHidden();
    await expect(toggle).toHaveAccessibleName("Assistant");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    const closedWidth = await mainWidth(page);

    await toggle.click();
    await expect(panel).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    // Opening by the toggle puts the cursor in the message box.
    await expect(messageBox(page)).toBeFocused();
    // The panel takes its column from the main content instead of covering it.
    const openWidth = await mainWidth(page);
    expect(closedWidth - openWidth).toBeGreaterThanOrEqual(380);
    const [mainBox, panelBox] = [await page.getByRole("main").boundingBox(), await panel.boundingBox()];
    expect((mainBox?.x ?? 0) + (mainBox?.width ?? 0)).toBeLessThanOrEqual(panelBox?.x ?? 0);

    // The toggle closes it again and keeps the focus.
    await toggle.click();
    await expect(panel).toBeHidden();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toBeFocused();
    expect(await mainWidth(page)).toBe(closedWidth);
  });

  test("the close button hides the panel and returns the focus to the toggle", async ({ page }) => {
    await mockApi(page);
    await loadDashboard(page);
    await openAssistant(page);

    await closeButton(page).click();

    await expect(assistantPanel(page)).toBeHidden();
    await expect(assistantToggle(page)).toBeFocused();
    await expect(assistantToggle(page)).toHaveAttribute("aria-expanded", "false");
  });

  test("the toggle is operable from the keyboard and large enough to hit", async ({ page }) => {
    await mockApi(page);
    await loadDashboard(page);

    const toggle = assistantToggle(page);
    const box = await toggle.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(24);
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(24);

    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(messageBox(page)).toBeFocused();
    await closeButton(page).focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toBeFocused();
  });

  test("closing keeps the conversation, the pending proposal and the unsent draft", async ({ page }) => {
    const api = await mockApi(page, { chatQueue: [chatProposal, chatSaved] });
    await loadDashboard(page);
    const panel = await openAssistant(page);
    await panel.getByRole("button", { name: PROMPT }).click();
    const confirmation = panel.getByRole("group", { name: "Plan awaiting your confirmation" });
    await expect(confirmation).toBeVisible();
    await messageBox(page).fill("an unsent thought");

    await closeButton(page).click();
    await expect(panel).toBeHidden();
    await openAssistant(page);

    await expect(panel.getByText(`You said: ${PROMPT}`)).toBeVisible();
    await expect(confirmation).toBeVisible();
    await expect(messageBox(page)).toHaveValue("an unsent thought");
    // The proposal is still the one of the same conversation and can be answered.
    await confirmation.getByRole("button", { name: "Yes, include it" }).click();
    await expect(panel.getByText("Added to your plans: Holiday closure")).toBeVisible();
    expect(api.requests.chat[1]).toMatchObject({ conversation_id: "conv-1" });
    await expect(messageBox(page)).toHaveValue("an unsent thought");
  });

  test("the last choice is remembered across a reload", async ({ page }) => {
    await mockApi(page);
    await loadDashboard(page);
    expect(await page.evaluate((key) => window.localStorage.getItem(key), STORAGE_KEY)).toBeNull();

    await openAssistant(page);
    expect(await page.evaluate((key) => window.localStorage.getItem(key), STORAGE_KEY)).toBe("true");
    await page.reload();
    await expect(assistantPanel(page)).toBeVisible();
    await expect(assistantToggle(page)).toHaveAttribute("aria-expanded", "true");
    // A panel that is open on load does not take the focus.
    await expect(messageBox(page)).not.toBeFocused();

    await closeButton(page).click();
    await page.reload();
    await expect(page.getByRole("article")).toHaveCount(4);
    await expect(assistantPanel(page)).toBeHidden();
    await expect(assistantToggle(page)).toHaveAttribute("aria-expanded", "false");
  });

  test("falls back to closed when the browser storage is unavailable", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException("Storage is disabled", "SecurityError");
        },
      });
    });
    await mockApi(page);
    await loadDashboard(page);

    await expect(assistantPanel(page)).toBeHidden();
    await openAssistant(page);
    await expect(messageBox(page)).toBeFocused();
  });

  test("the closed toggle signals a reply in progress and a proposal awaiting confirmation", async ({ page }) => {
    await mockApi(page, { chatQueue: [chatProposal, chatSaved], chatDelayMs: 1200 });
    await loadDashboard(page);
    const toggle = assistantToggle(page);
    const dot = toggle.locator(".assistant-toggle__dot");
    await expect(dot).toHaveCount(0);

    const panel = await openAssistant(page);
    await panel.getByRole("button", { name: PROMPT }).click();
    // Nothing is signalled while the panel is open.
    await expect(dot).toHaveCount(0);
    await closeButton(page).click();
    await expect(toggle).toHaveAccessibleName("Assistant is working on a reply");
    await expect(dot).toBeVisible();

    // The reply arrives while the panel is closed: a plan now waits for the owner's answer.
    await expect(toggle).toHaveAccessibleName("Assistant needs your confirmation");
    await expect(dot).toBeVisible();
    await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze()
      .then((results) => expect(results.violations.map((violation) => violation.id)).toEqual([]));

    await toggle.click();
    await expect(dot).toHaveCount(0);
    const confirmation = panel.getByRole("group", { name: "Plan awaiting your confirmation" });
    await expect(confirmation).toBeInViewport({ ratio: 1 });
    await confirmation.getByRole("button", { name: "Yes, include it" }).click();
    await expect(panel.getByText("Added to your plans: Holiday closure")).toBeVisible();
    await closeButton(page).click();
    // Answered: nothing waits any more.
    await expect(toggle).toHaveAccessibleName("Assistant");
    await expect(dot).toHaveCount(0);
  });

  test("a message that could not be delivered is signalled on the closed toggle", async ({ page }) => {
    // An empty chat queue answers 503: the reply fails while the panel is closed.
    await mockApi(page, { chatDelayMs: 600 });
    await loadDashboard(page);
    const toggle = assistantToggle(page);
    const panel = await openAssistant(page);
    await panel.getByRole("button", { name: PROMPT }).click();
    await closeButton(page).click();
    await expect(toggle).toHaveAccessibleName("Assistant is working on a reply");

    await expect(toggle).toHaveAccessibleName("Assistant could not deliver your message");

    await toggle.click();
    await expect(panel.getByRole("button", { name: "Try again" })).toBeVisible();
    await expect(toggle).toHaveAccessibleName("Assistant");
  });

  test("a removal awaiting confirmation is signalled too", async ({ page }) => {
    await mockApi(page, { chatQueue: [chatRemovalProposal] });
    await loadDashboard(page);
    const panel = await openAssistant(page);
    await messageBox(page).fill("Please remove the oven purchase");
    await messageBox(page).press("Enter");
    await expect(panel.getByRole("group", { name: "Plan awaiting removal" })).toBeVisible();

    await closeButton(page).click();

    await expect(assistantToggle(page)).toHaveAccessibleName("Assistant needs your confirmation");
  });
});

test.describe("assistant panel on a phone (375 px)", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("starts closed; the toggle shows it under the content and hides it again", async ({ page }) => {
    await mockApi(page);
    await loadDashboard(page);
    const toggle = assistantToggle(page);
    const panel = assistantPanel(page);
    await expect(panel).toBeHidden();
    await expect(toggle).toBeInViewport({ ratio: 1 });
    await expect(page.getByRole("link", { name: /assistant/i })).toHaveCount(0);

    await toggle.click();
    await expect(panel).toBeInViewport({ ratio: 1 });
    await expect(messageBox(page)).toBeFocused();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    await closeButton(page).click();
    await expect(panel).toBeHidden();
    await expect(toggle).toBeFocused();
    await expect(toggle).toBeInViewport({ ratio: 1 });
  });

  test("a plan saved in the chat brings the updated forecast into view", async ({ page }) => {
    await mockApi(page, { chatQueue: [chatProposal, chatSaved] });
    await loadDashboard(page);
    const panel = await openAssistant(page);
    await panel.getByRole("button", { name: PROMPT }).click();
    await panel.getByRole("button", { name: "Yes, include it" }).click();

    await expect(page.getByText("Status: Shortfall ahead")).toBeInViewport();
    await expect(panel).toBeVisible();
  });

  test("the toggle leads back to an open panel that is out of sight instead of hiding it", async ({ page }) => {
    await mockApi(page);
    await loadDashboard(page);
    const toggle = assistantToggle(page);
    const panel = await openAssistant(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(panel).not.toBeInViewport();

    await toggle.click();

    await expect(panel).toBeInViewport({ ratio: 1 });
    await expect(messageBox(page)).toBeFocused();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  test("a panel remembered as open is one tap away on the next visit", async ({ page }) => {
    await page.addInitScript((key) => window.localStorage.setItem(key, "true"), STORAGE_KEY);
    await mockApi(page);
    await loadDashboard(page);
    const panel = assistantPanel(page);
    await expect(panel).toBeVisible();
    await expect(panel).not.toBeInViewport();

    await assistantToggle(page).click();

    await expect(panel).toBeInViewport({ ratio: 1 });
    await expect(messageBox(page)).toBeFocused();
  });
});
