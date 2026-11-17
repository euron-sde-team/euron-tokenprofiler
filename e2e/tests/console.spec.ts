import { expect, test } from "@playwright/test";
import { API, H, KEY, makeKey, openAs, openConsole, RUN, sendEvents } from "../helpers";

test("connect screen rejects a bad key and accepts the right one", async ({ page }) => {
  await openConsole(page, "/");
  await page.getByRole("button", { name: "Disconnect" }).click(); // the demo build pre-fills the key
  await page.getByPlaceholder("tp_…").fill("tp_bad00000_wrong");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.locator("form [role=alert]")).toContainText(/invalid or revoked/i);
  await page.getByPlaceholder("tp_…").fill(KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
});

test("overview shows spend, projection, trend chart, top lists and budgets", async ({ page }) => {
  await openConsole(page, "/");
  await expect(page.getByRole("region", { name: "Key numbers" })).toContainText("Spend");
  await expect(page.getByRole("region", { name: "Key numbers" })).toContainText(/\$\d/);
  await expect(page.getByTestId("stacked-bars")).toBeVisible();
  await expect(page.getByRole("region", { name: "Top apps" }).getByTestId("hbars")).toContainText("support-bot");
  await expect(page.getByTestId("budget-mini").first()).toBeVisible();
  await expect(page.getByTestId("optimizer-teaser")).toContainText(/ways to save/);
  await expect(page.getByTestId("sidebar-savings")).toContainText("/mo");
});

test("the range and app filters live in the URL and narrow the numbers", async ({ page }) => {
  await openConsole(page, "/");
  const spend = async () => (await page.getByTestId("stat-spend").textContent()) ?? "";
  const all = await spend();
  await page.getByLabel("App", { exact: true }).selectOption("code-review");
  await expect(page).toHaveURL(/app=code-review/);
  await expect.poll(spend).not.toBe(all);
  await page.getByLabel("Time range").selectOption("7d");
  await expect(page).toHaveURL(/range=7d/);
  await page.reload();
  await expect(page.getByLabel("App", { exact: true })).toHaveValue("code-review");
});

test("explorer groups by any dimension and exports CSV", async ({ page }) => {
  await openConsole(page, "/explorer");
  await expect(page.getByTestId("explorer-row").first()).toBeVisible();
  await page.getByLabel("Group by").selectOption("app");
  await expect(page.getByTestId("explorer-row").filter({ hasText: "support-bot" })).toBeVisible();
  await page.getByLabel("Group by").selectOption("tag:team");
  await expect(page.getByTestId("explorer-row").filter({ hasText: "devex" })).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).click()]);
  expect(download.suggestedFilename()).toBe("tokenprofiler-events.csv");
});

test("models: what the same traffic would cost on every other model", async ({ page }) => {
  await openConsole(page, "/models?app=support-bot");
  await expect(page.getByTestId("actual-cost")).toContainText(/\$\d/);
  const mini = page.getByTestId("candidate-row").filter({ has: page.locator('td:text-is("gpt-4o-mini")') });
  await expect(mini).toBeVisible();
  await expect(mini).toContainText("-");
});

test("prompts: ranked templates with a drill-down", async ({ page }) => {
  await openConsole(page, "/prompts");
  await page.getByTestId("prompt-row").filter({ hasText: "support-faq" }).first().click();
  await expect(page.getByTestId("prompt-title")).toHaveText("support-faq");
  await expect(page.getByTestId("prompt-detail")).toContainText("Latency p50 / p95");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("prompt-detail")).toHaveCount(0);
});

test("events: filter by status, open one, load more", async ({ page }) => {
  await openConsole(page, "/events");
  await expect(page.getByTestId("event-row").first()).toBeVisible();
  await page.getByLabel("Status").selectOption("error");
  await expect(page).toHaveURL(/status=error/);
  await expect(page.getByTestId("event-row").first()).toContainText(/rate_limit|timeout|http_|Error/);
  await page.getByTestId("event-row").first().click();
  await expect(page.getByTestId("event-detail")).toContainText("Input cost");
  await page.keyboard.press("Escape");
  await page.getByLabel("Status").selectOption("");
  await page.getByRole("button", { name: "Load more" }).click();
  await expect.poll(() => page.getByTestId("event-row").count()).toBeGreaterThan(50);
});

test("optimizer: recommendations with evidence, dismiss and restore", async ({ page }) => {
  await openConsole(page, "/optimizer");
  await expect(page.getByTestId("potential")).toContainText(/\$\d/);
  const cards = page.getByTestId("recommendation");
  await expect(cards.first()).toBeVisible();
  await expect(cards.filter({ hasText: "support-bot" }).first()).toContainText(/cheaper|gpt-4o-mini/i);
  const first = cards.first();
  await first.getByRole("button", { name: /Show evidence/ }).click();
  await expect(first.getByTestId("rec-details")).toContainText("What to do");
  const title = (await first.locator("h3").textContent()) ?? "";
  const before = await cards.count();
  await first.getByRole("button", { name: /^Dismiss/ }).click();
  await expect(cards).toHaveCount(before - 1);
  await page.getByLabel(/Show dismissed/).check();
  const restored = cards.filter({ hasText: title });
  await expect(restored).toBeVisible();
  await restored.getByRole("button", { name: "Restore" }).click();
  await page.getByLabel(/Show dismissed/).uncheck();
  await expect(cards).toHaveCount(before);
});

test("prompt lab: findings, savings and an optimized prompt", async ({ page }) => {
  await openConsole(page, "/lab");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByTestId("lab-summary")).toBeVisible();
  await expect(page.getByTestId("finding").first()).toBeVisible();
  await expect(page.getByTestId("lab-savings")).toContainText(/\$\d/);
  await expect(page.getByTestId("optimized")).toBeVisible();
  expect(await page.getByTestId("lab-price-row").count()).toBeGreaterThan(3);
  await page.getByLabel("Prompt text").fill("Classify the sentiment of this review as positive or negative.");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByTestId("lab-clean")).toBeVisible();
});

test("budgets: create one, see it exceeded, get the alert, acknowledge it", async ({ page, request }) => {
  const app = `pw-${RUN}`;
  await sendEvents(request, Array.from({ length: 20 }, (_, i) => ({ id: `${app}-${i}`, model: "gpt-4o", app, prompt_tokens: 1, completion_tokens: 40_000 })));
  await openConsole(page, "/budgets");
  await page.getByRole("button", { name: "New budget" }).first().click();
  await page.getByLabel("Budget name").fill(`Playwright ${RUN}`);
  await page.getByLabel("Monthly limit").fill("5");
  await page.getByLabel("Applies to").selectOption("app");
  await page.getByLabel("Scope value").fill(app);
  await page.getByRole("button", { name: "Create budget" }).click();
  const card = page.getByTestId("budget").filter({ hasText: `Playwright ${RUN}` });
  await expect(card).toHaveAttribute("data-status", "exceeded");
  await page.getByRole("button", { name: "Check now" }).click();
  const alert = page.getByTestId("alert").filter({ hasText: `Playwright ${RUN}` });
  await expect(alert).toBeVisible();
  await alert.getByRole("button", { name: /Acknowledge/ }).click();
  await expect(alert).toHaveCount(0);
  await card.getByRole("button", { name: /^Delete/ }).click();
  await expect(card).toHaveCount(0);
});

test("settings: snippets, a scoped key shown once then revoked, and a price override", async ({ page, request }) => {
  await openConsole(page, "/settings");
  await expect(page.getByTestId("snippet")).toContainText("tokenprofiler");
  await page.getByRole("tab", { name: /Proxy/ }).click();
  await expect(page.getByTestId("snippet")).toContainText("/proxy/v1");

  await page.getByRole("button", { name: "New key" }).click();
  await page.getByLabel("Key name").fill(`pw-${RUN}`);
  await page.getByLabel("Limit to apps").fill("support-bot");
  await page.getByRole("button", { name: "Create key" }).click();
  const shown = (await page.getByTestId("new-key").textContent()) ?? "";
  expect(shown).toMatch(/^tp_[a-f0-9]{8}_/);
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.getByTestId("new-key")).toHaveCount(0);
  const row = page.getByTestId("key-row").filter({ hasText: `pw-${RUN}` });
  await expect(row).toContainText("support-bot");
  expect(await row.textContent()).not.toContain(shown);
  await row.getByRole("button", { name: `Revoke pw-${RUN}` }).click();
  await expect(row).toContainText("revoked");
  expect((await fetch(`${API}/api/overview`, { headers: { Authorization: `Bearer ${shown}` } })).status).toBe(401);

  await page.getByLabel("Filter prices").fill("gpt-4o-mini");
  const price = page.getByTestId("price-row").filter({ has: page.locator('td:text-is("gpt-4o-mini")') });
  await price.getByRole("button", { name: "Override gpt-4o-mini" }).click();
  await page.getByLabel("Input price").fill("0.2");
  await page.getByRole("button", { name: "Save and re-price history" }).click();
  await expect(price).toContainText("override");
  await price.getByRole("button", { name: /Remove override/ }).click();
  await expect(price).not.toContainText("override");
  void request;
});

test("an app-restricted key sees only its own app in the console", async ({ page, request }) => {
  const { key } = await makeKey(request, `scoped-${RUN}`, ["read"], ["docs-assistant"]);
  await openAs(page, key, "/");
  await expect(page.getByRole("region", { name: "Top apps" }).getByTestId("hbars")).toContainText("docs-assistant");
  await expect(page.getByRole("region", { name: "Top apps" })).not.toContainText("support-bot");
  await page.goto("/explorer?by=app");
  await expect(page.getByTestId("explorer-row")).toHaveCount(1);
  await page.goto("/settings");
  await expect(page.getByTestId("keys-forbidden")).toContainText("admin");
});

test("theme toggle switches and persists across reloads", async ({ page }) => {
  await openConsole(page, "/");
  const html = page.locator("html");
  const before = (await html.getAttribute("data-theme")) as "dark" | "light";
  const after = before === "dark" ? "light" : "dark";
  await page.getByRole("button", { name: new RegExp(`Switch to ${after} theme`) }).click();
  await expect(html).toHaveAttribute("data-theme", after);
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", after);
});

test("mobile layout has navigation and no horizontal page scroll on any page", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await openConsole(page, "/");
  for (const path of ["/", "/explorer", "/models", "/prompts", "/events", "/optimizer", "/lab", "/budgets", "/settings"]) {
    await page.goto(path);
    await expect(page.getByRole("navigation", { name: "Main (mobile)" })).toBeVisible();
    await page.waitForTimeout(400);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${path} scrolls horizontally`).toBeLessThanOrEqual(1);
  }
});

test("disconnect returns to the connect screen", async ({ page }) => {
  await openConsole(page, "/");
  await page.getByRole("button", { name: "Disconnect" }).click();
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeVisible();
  void H;
});
