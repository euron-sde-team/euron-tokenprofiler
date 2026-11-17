import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./screenshots",
  timeout: 180_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: process.env.TP_CONSOLE_URL ?? "http://localhost:3300", deviceScaleFactor: 1 },
  projects: [{ name: "shots", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
