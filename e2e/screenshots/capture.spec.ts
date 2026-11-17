// Regenerates docs/assets/screenshots/*.png from a running, freshly seeded stack (`make up && make demo`).
import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { openConsole } from "../helpers";

const OUT = resolve(__dirname, "../../docs/assets/screenshots");
const shot = (page: Page, name: string) => page.screenshot({ path: `${OUT}/${name}.png` });

test("capture every screen", async ({ page }) => {
  await openConsole(page);
  await page.evaluate(() => localStorage.setItem("tokenprofiler.theme", "dark"));
  await page.goto("/");
  await expect(page.getByTestId("stacked-bars")).toBeVisible();
  await expect(page.getByTestId("budget-mini").first()).toBeVisible();
  await page.waitForTimeout(1500);
  await shot(page, "01-overview");

  await page.goto("/explorer?by=app");
  await expect(page.getByTestId("explorer-row").first()).toBeVisible();
  await page.waitForTimeout(800);
  await shot(page, "02-explorer");

  await page.goto("/models?app=support-bot");
  await expect(page.getByTestId("candidate-row").first()).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "03-models");

  await page.goto("/prompts");
  await page.getByTestId("prompt-row").filter({ hasText: "support-faq" }).first().click();
  await expect(page.getByTestId("prompt-detail")).toBeVisible();
  await page.waitForTimeout(800);
  await shot(page, "04-prompts");

  await page.goto("/optimizer");
  const first = page.getByTestId("recommendation").first();
  await expect(first).toBeVisible();
  await first.getByRole("button", { name: /Show evidence/ }).click();
  await page.waitForTimeout(500);
  await shot(page, "05-optimizer");

  await page.goto("/lab");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByTestId("lab-summary")).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "06-lab");

  await page.goto("/budgets");
  await expect(page.getByTestId("budget").first()).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "07-budgets");

  await page.goto("/events");
  await expect(page.getByTestId("event-row").first()).toBeVisible();
  await page.getByTestId("event-row").nth(2).click();
  await expect(page.getByTestId("event-detail")).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "08-events");

  await page.goto("/settings");
  await expect(page.getByTestId("snippet")).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "09-settings");

  await page.evaluate(() => localStorage.setItem("tokenprofiler.theme", "light"));
  await page.goto("/");
  await expect(page.getByTestId("stacked-bars")).toBeVisible();
  await page.waitForTimeout(1200);
  await shot(page, "10-overview-light");
  await page.evaluate(() => localStorage.setItem("tokenprofiler.theme", "dark"));
});
