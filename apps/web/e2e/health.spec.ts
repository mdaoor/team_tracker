import { expect, test } from "@playwright/test";

test("status page renders", async ({ page }) => {
  await page.goto("/status");
  await expect(page.getByRole("heading", { name: "Team Tracker" })).toBeVisible();
  await expect(page.getByTestId("status")).toContainText("API ready");
});
