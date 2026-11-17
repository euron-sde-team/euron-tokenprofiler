import { expect, test } from "@playwright/test";
import { API, H, openAs, openConsole, RUN, sendEvents, trackErrors } from "../helpers";

test("an unknown route renders a 404 inside the app", async ({ page }) => {
  await openConsole(page, "/nope");
  await expect(page.getByText("Page not found")).toBeVisible();
  await page.getByRole("link", { name: "Back to the overview" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
});

test("hostile text in app, prompt and key names is rendered as text, never as HTML (XSS)", async ({ page, request }) => {
  const payload = `<img src=x onerror="window.__xss=1">`;
  await request.post(`${API}/api/keys`, { headers: H, data: { name: `${payload}xss-${RUN}`, scopes: ["read"] } });
  await sendEvents(request, [{ id: `xss-${RUN}`, model: "gpt-4o", app: `xss-app-${RUN}`, prompt_id: `${payload}tpl-${RUN}`, user_id: payload, prompt_tokens: 10, completion_tokens: 1 }]);
  await openConsole(page, "/settings");
  await expect(page.getByTestId("key-row").filter({ hasText: `xss-${RUN}` })).toContainText("<img");
  await openConsole(page, `/prompts?app=xss-app-${RUN}`);
  await expect(page.getByTestId("prompt-row").first()).toContainText("<img");
  await openConsole(page, `/events?app=xss-app-${RUN}`);
  await page.getByTestId("event-row").first().click();
  await expect(page.getByTestId("event-detail")).toContainText("<img");
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
  expect(await page.locator("img[src=x]").count()).toBe(0);
});

test("a revoked key shows an auth error instead of hanging", async ({ page, request }) => {
  const r = await request.post(`${API}/api/keys`, { headers: H, data: { name: `rv-${RUN}`, scopes: ["read"] } });
  const { key, prefix } = (await r.json()) as { key: string; prefix: string };
  await openAs(page, key, "/events");
  await request.delete(`${API}/api/keys/${prefix}`, { headers: H });
  await page.reload();
  await expect(page.getByRole("alert").first()).toContainText(/invalid or revoked|Unreachable|API key/i);
});

test("an API outage is reported, then the console recovers on its own", async ({ page }) => {
  await openConsole(page, "/");
  await expect(page.getByText("Connected")).toBeVisible();
  await page.route("**/api/system", (route) => route.abort());
  await expect(page.getByText("Unreachable")).toBeVisible({ timeout: 30_000 });
  await page.unroute("**/api/system");
  await expect(page.getByText("Connected")).toBeVisible({ timeout: 30_000 });
});

test("an empty range shows an empty state, not a broken chart", async ({ page }) => {
  await openConsole(page, "/?app=does-not-exist");
  await expect(page.getByText("No usage in this range")).toBeVisible();
  await openConsole(page, "/optimizer?app=does-not-exist");
  await expect(page.getByText("Nothing to optimize yet")).toBeVisible();
});

test("no console errors across every page", async ({ page }) => {
  const errors = trackErrors(page);
  await openConsole(page, "/");
  for (const path of ["/", "/explorer", "/models", "/prompts", "/events", "/optimizer", "/lab", "/budgets", "/settings"]) {
    await page.goto(path);
    await expect(page.getByRole("navigation", { name: /^Main/ })).toBeVisible();
    await page.waitForTimeout(600);
  }
  expect(errors).toEqual([]);
});

test("keyboard: Enter opens a prompt and Escape closes it", async ({ page }) => {
  await openConsole(page, "/prompts");
  const row = page.getByTestId("prompt-row").first();
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("prompt-detail")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("prompt-detail")).toHaveCount(0);
});

test("the page title and landmarks are present", async ({ page }) => {
  await openConsole(page, "/");
  await expect(page).toHaveTitle(/Euron TokenProfiler/);
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
});
