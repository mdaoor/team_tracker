import { expect, test } from "@playwright/test";

test("the plan-only ERP project shows its roadmap, a date edit sticks, and Present this plan opens a deck", async ({
  page,
}) => {
  await page.goto("/w/acme/plan/ERP");
  const table = page.getByRole("table", { name: "Roadmap" });
  await expect(table).toBeVisible();
  await expect(table.getByRole("row")).not.toHaveCount(1);
  const end = table.locator('input[type="date"]:not([disabled])').nth(1);
  const before = await end.inputValue();
  const next = before.slice(0, 8) + (before.endsWith("28") ? "27" : "28");
  const saved = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && r.url().includes("/items/"),
  );
  await end.fill(next);
  await end.blur();
  expect((await saved).status()).toBe(200);
  await page.reload();
  await expect(table.locator('input[type="date"]:not([disabled])').nth(1)).toHaveValue(next);
  await page.getByRole("button", { name: "Present this plan" }).click();
  await expect(page).toHaveURL(/\/present\/[^/]+$/);
  await expect(page.getByLabel("Title").first()).toHaveValue(/plan review|ERP/i);
});

test("an end before the start is refused inline", async ({ page }) => {
  await page.goto("/w/acme/plan/ERP");
  const start = page.locator('input[type="date"]:not([disabled])').first();
  await start.fill("2099-01-01");
  await start.blur();
  await expect(page.getByRole("alert")).toContainText("before the start");
});
