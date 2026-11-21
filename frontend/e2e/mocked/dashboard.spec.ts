import { expect, test } from "@playwright/test";
import {
  approvedDashboard,
  closureEvent,
  fallbackRecommendation,
  greenDashboard,
  redDashboard,
} from "../fixtures/data";
import { apiError, mockApi } from "../fixtures/mockApi";

test.describe("dashboard", () => {
  test("renders the RED state with figures, chart, explanation and options", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { level: 1, name: "Autonomous CFO" })).toBeVisible();
    const hero = page.getByRole("region", { name: /€22,000 short/ });
    await expect(hero.getByText("Shortfall ahead")).toBeVisible();
    await expect(hero.getByText("€3,205.69")).toBeVisible();
    await expect(hero.getByText("-€22,097.60")).toBeVisible();
    await expect(hero.getByText("on 3 Jul 2025")).toBeVisible();
    await expect(hero.getByText("in about 8 weeks", { exact: true })).toBeVisible();

    // The headline is the engine's: it does not change when the AI explanation arrives.
    await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();
    await expect(page.getByText(/no revenue, while salaries, rent/)).toBeVisible();
    await expect(page.getByText(/calculated by deterministic code/)).toBeVisible();

    await expect(page.getByRole("heading", { name: /^Balance: past 90 days and next 60 days$/ })).toBeVisible();
    await expect(page.locator(".recharts-line")).toHaveCount(3);
    await expect(page.getByText(/lowest point of -€22,097.60 on 3 Jul 2025/)).toBeVisible();

    const options = page.getByRole("article");
    await expect(options).toHaveCount(4);
    const recommended = page.getByRole("article", { name: /Option E/ });
    await expect(recommended.getByText("Recommended")).toBeVisible();
    await expect(recommended.getByText("Cheapest way to stay safe.")).toBeVisible();
    await expect(recommended.getByText("€61.85")).toBeVisible();
    await expect(page.getByText(/Why option E:/)).toBeVisible();
    await expect(page.getByRole("article", { name: /Option B/ }).getByText("No cost")).toBeVisible();
  });

  test("shows the dashboard immediately while the assistant is still analysing", async ({ page }) => {
    await mockApi(page, { recommendationDelayMs: 1500 });
    await page.goto("/");

    await expect(page.getByRole("heading", { name: redDashboard.headline })).toBeVisible();
    await expect(page.getByText("The assistant is analysing your forecast…")).toBeVisible();
    await expect(page.getByRole("article", { name: /Option E/ }).getByText("Recommended")).toBeVisible();
    await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();
  });

  test("labels a fallback explanation as not written by the AI", async ({ page }) => {
    await mockApi(page, { recommendation: fallbackRecommendation });
    await page.goto("/");

    await expect(page.getByText("Standard explanation (AI unavailable)")).toBeVisible();
    await expect(page.getByText("Why no AI text: The language model did not respond in time.")).toBeVisible();
    await expect(page.getByText("AI explanation", { exact: true })).toHaveCount(0);
  });

  test("the Why? toggle reveals the fixed costs inside the risk window", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    const why = page.getByRole("button", { name: "Why?" });
    await expect(why).toHaveAttribute("aria-expanded", "false");
    await why.click();
    const row = page.getByRole("row", { name: /Transfers to Zurich/ });
    await expect(row).toContainText("€12,000.00");
    await expect(row).toContainText("Can be postponed");
    await expect(page.getByRole("row", { name: /Salaries/ })).toContainText("Must be paid");
  });

  test("previewing an option draws its line on the chart", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    const legend = page.getByRole("list", { name: "Chart legend" });
    await expect(legend).toContainText("Forecast with option E");

    const optionC = page.getByRole("article", { name: /Option C/ });
    await optionC.getByRole("button", { name: "Preview on chart" }).click();
    await expect(optionC.getByRole("button", { name: "Shown on chart" })).toHaveAttribute("aria-pressed", "true");
    await expect(legend).toContainText("Forecast with option C: Shorter holiday");
    await expect(page.getByText(/With option C \(Shorter holiday\) the lowest point is -€8,420.10/)).toBeVisible();

    await page.getByText("Show the forecast as a table").click();
    await expect(page.getByRole("columnheader", { name: "With option C" })).toBeVisible();
  });

  test("shows a calm state with a surplus insight when the outlook is GREEN", async ({ page }) => {
    await mockApi(page, { dashboard: greenDashboard, events: [] });
    await page.goto("/");

    await expect(page.getByText("Status: On track")).toBeVisible();
    await expect(page.getByText("No action needed")).toBeVisible();
    await expect(page.getByText("You have money you could put to work")).toBeVisible();
    await expect(page.getByRole("article")).toHaveCount(0);
    await expect(page.getByText(/No plans yet/)).toBeVisible();
    await expect(page.getByText(/No decisions yet/)).toBeVisible();
  });

  test("shows the credit limit once a credit line is in place", async ({ page }) => {
    await mockApi(page, { dashboard: approvedDashboard, events: approvedDashboard.events });
    await page.goto("/");

    const hero = page.getByRole("region", { name: approvedDashboard.headline });
    await expect(hero.getByText("Credit line", { exact: true })).toBeVisible();
    await expect(hero.getByText("€15,000.00")).toBeVisible();
    await expect(page.getByText("From an approved option").first()).toBeVisible();
  });

  test("changing horizon, buffer and date reloads the dashboard", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("article")).toHaveCount(4);

    await page.getByRole("radio", { name: "90 days" }).check({ force: true });
    await expect.poll(() => api.requests.dashboard.at(-1)?.get("horizon_days")).toBe("90");

    await page.getByLabel("Safety buffer (€)").fill("5000");
    await expect.poll(() => api.requests.dashboard.at(-1)?.get("buffer_eur")).toBe("5000");

    await page.getByLabel("Today").fill("2025-12-01");
    await expect.poll(() => api.requests.dashboard.at(-1)?.get("as_of_date")).toBe("2025-12-01");
    await expect(page.getByText("“Today” is now 1 Dec 2025. Started a new conversation.")).toBeVisible();
  });

  test("rejects an invalid buffer without calling the API", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("article")).toHaveCount(4);
    const callsBefore = api.requests.dashboard.length;

    await page.getByLabel("Safety buffer (€)").fill("-5");
    await expect(page.getByText("Enter 0 to 1,000,000")).toBeVisible();
    await page.waitForTimeout(800);
    expect(api.requests.dashboard.length).toBe(callsBefore);
  });

  test("rejects a buffer above the API limit inline", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("article")).toHaveCount(4);
    const callsBefore = api.requests.dashboard.length;

    await page.getByLabel("Safety buffer (€)").fill("1e9");
    await expect(page.getByText("Enter 0 to 1,000,000")).toBeVisible();
    await page.waitForTimeout(800);
    expect(api.requests.dashboard.length).toBe(callsBefore);
  });

  test.describe("keyboard date entry", () => {
    test.use({ locale: "en-GB" });

    test("typing a date on the keyboard loads the forecast once and resets the chat once", async ({ page }) => {
      const api = await mockApi(page);
      await page.goto("/");
      await expect(page.getByRole("article")).toHaveCount(4);
      const callsBefore = api.requests.dashboard.length;

      const today = page.getByLabel("Today");
      await today.focus();
      // Day, month, year: the segment order of the en-GB locale this describe block runs in.
      await page.keyboard.type("12062025", { delay: 100 });
      await expect(today).toHaveValue("2025-06-12");

      await expect(page.getByText("“Today” is now 12 Jun 2025. Started a new conversation.")).toBeVisible();
      await page.waitForTimeout(800);
      const requestedDates = api.requests.dashboard.slice(callsBefore).map((query) => query.get("as_of_date"));
      expect(requestedDates).toEqual(["2025-06-12"]);
    });
  });

  test("an out-of-range date is rejected inline and the dashboard stays", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("article")).toHaveCount(4);
    const callsBefore = api.requests.dashboard.length;

    const today = page.getByLabel("Today");
    await today.fill("2026-03-01");
    await expect(page.getByText("Choose a date within 1 Jan – 31 Dec 2025")).toBeVisible();
    await expect(today).toHaveAttribute("aria-invalid", "true");
    await page.waitForTimeout(800);
    expect(api.requests.dashboard.length).toBe(callsBefore);
    await expect(page.getByRole("heading", { name: "Your options" })).toBeVisible();
  });

  test("a failed reload keeps the last forecast on screen and blocks decisions on it", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("article")).toHaveCount(4);

    api.state.dashboard = apiError(503, "database_unavailable", "The database is not reachable.");
    await page.getByRole("radio", { name: "90 days" }).check({ force: true });

    const alert = page.getByRole("alert").filter({ hasText: "Your forecast could not be updated" });
    await expect(alert).toContainText("The database is not reachable.");
    await expect(page.getByRole("heading", { name: "Your options" })).toBeVisible();
    await expect(page.getByRole("article")).toHaveCount(4);
    await expect(page.getByRole("button", { name: "Approve option A" })).toBeDisabled();

    api.state.dashboard = redDashboard;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(alert).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Approve option A" })).toBeEnabled();
  });

  test("a closure is shaded for every closed day, including a one-day closure", async ({ page }) => {
    const oneDay = { ...closureEvent, id: 31, date_from: "2025-06-02", date_to: "2025-06-02" };
    const threeDays = { ...closureEvent, id: 32, date_from: "2025-06-10", date_to: "2025-06-12" };
    await mockApi(page, { dashboard: { ...redDashboard, events: [oneDay, threeDays] } });
    await page.goto("/");

    const bands = page.locator(".recharts-reference-area-rect");
    await expect(bands).toHaveCount(2);
    const widths = await bands.evaluateAll((rects) => rects.map((rect) => rect.getBoundingClientRect().width));
    const [oneDayWidth = 0, threeDayWidth = 0] = widths;
    expect(oneDayWidth).toBeGreaterThan(1);
    expect(threeDayWidth / oneDayWidth).toBeCloseTo(3, 1);
  });

  test("once nothing needs doing the chart no longer says “If you do nothing”", async ({ page }) => {
    await mockApi(page, { dashboard: approvedDashboard, events: approvedDashboard.events });
    await page.goto("/");

    await expect(page.getByText(/The forecast for the next 60 days reaches its lowest point of -€10,097.60/)).toBeVisible();
    await expect(page.getByText(/If you do nothing/)).toHaveCount(0);
    await expect(page.locator(".recharts-wrapper").getByText("Safety buffer (€3,000 above the credit limit)")).toBeVisible();
    await page.getByText("Show the forecast as a table").click();
    await expect(page.getByRole("columnheader", { name: "Forecast", exact: true })).toBeVisible();
  });

  test("shows the server error and recovers on retry", async ({ page }) => {
    const api = await mockApi(page, {
      dashboard: apiError(400, "as_of_date_out_of_range", "as_of_date must be within 2025-01-01 and 2025-12-31."),
    });
    await page.goto("/");

    const alert = page.getByRole("alert").filter({ hasText: "Your forecast could not be loaded" });
    await expect(alert).toContainText("as_of_date must be within 2025-01-01 and 2025-12-31.");
    await expect(alert).toContainText("as_of_date_out_of_range");

    api.state.dashboard = redDashboard;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("heading", { name: /€22,000 short/ })).toBeVisible();
    await expect(alert).toHaveCount(0);
  });

  test("reports a non-contract failure of the recommendation endpoint", async ({ page }) => {
    await mockApi(page, { recommendation: apiError(502, "bad_gateway", "Upstream unavailable.") });
    await page.goto("/");

    await expect(page.getByRole("alert").filter({ hasText: "The explanation could not be loaded" })).toContainText(
      "Upstream unavailable.",
    );
    // The deterministic parts keep working: headline, options and the code-ranked recommendation.
    await expect(page.getByRole("heading", { name: redDashboard.headline })).toBeVisible();
    await expect(page.getByRole("article", { name: /Option E/ }).getByText("Recommended")).toBeVisible();
  });
});
