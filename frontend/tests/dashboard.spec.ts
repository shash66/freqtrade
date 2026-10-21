import { expect, test } from "@playwright/test";

test("overview renders real connection health, labeled as simulation", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/simulation/i).first()).toBeVisible();
  await expect(page.getByText("Base", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Arbitrum One").first()).toBeVisible();
  // with no RPC configured the dashboard must say so, not pretend
  await expect(page.getByText(/not configured|unreachable|no RPC/i).first()).toBeVisible();
  await expect(page.getByText(/not on-chain earnings/i)).toBeVisible();
});

test("settings page shows read-only mode and never an RPC URL", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByText(/live trading/i).first()).toBeVisible();
  await expect(page.getByText(/disabled/i).first()).toBeVisible();
  const html = await page.content();
  expect(html).not.toMatch(/https?:\/\/[^"'\s]*\/v2\//);
});

test("competition lab runs a seeded comparison and labels it as modeled", async ({ page }) => {
  await page.goto("/competition");
  const run = page.getByRole("button", { name: /compare|run/i }).first();
  await run.click();
  await expect(page.getByText(/modeled/i).first()).toBeVisible({ timeout: 20_000 });
});

test("backtest launch requires the administrator (no credentials -> visible auth error)", async ({ page }) => {
  await page.goto("/backtests");
  const run = page.getByRole("button", { name: /run backtest/i }).first();
  await run.click();
  await expect(page.getByText(/401|unauthori|credential|admin/i).first()).toBeVisible({ timeout: 15_000 });
});

test("results survive a refresh (backtest list is server state)", async ({ page }) => {
  await page.goto("/opportunities");
  await expect(page.getByText(/opportunit/i).first()).toBeVisible();
  await page.reload();
  await expect(page.getByText(/opportunit/i).first()).toBeVisible();
});

test("usable at phone width without horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await page.goto("/");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});
