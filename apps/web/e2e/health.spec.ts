import { expect, test } from "@playwright/test";

test("health page renders", async ({ page }) => {
  await page.goto("/healthz");
  await expect(page.getByRole("heading", { name: "Team Tracker" })).toBeVisible();
});
