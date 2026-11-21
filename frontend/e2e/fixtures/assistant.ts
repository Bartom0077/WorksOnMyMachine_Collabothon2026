import { type Locator, type Page, expect } from "@playwright/test";

/** The top-bar button that shows and hides the assistant; its name can be followed by what the assistant waits for. */
export function assistantToggle(page: Page): Locator {
  return page.getByRole("button", { name: /^Assistant/ });
}

export function assistantPanel(page: Page): Locator {
  return page.getByRole("complementary", { name: "Assistant" });
}

/** The assistant starts closed: opens it from the top bar and returns the panel. */
export async function openAssistant(page: Page): Promise<Locator> {
  await assistantToggle(page).click();
  const panel = assistantPanel(page);
  await expect(panel).toBeVisible();
  return panel;
}
