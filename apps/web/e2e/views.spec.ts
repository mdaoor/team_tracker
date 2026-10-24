import { expect, test } from "@playwright/test";

test("layout tabs switch and filter chips round-trip through the URL", async ({ page }) => {
  await page.goto("/w/acme/views");
  await expect(page.getByRole("toolbar", { name: "View options" })).toBeVisible();
  await page.getByRole("tab", { name: "matrix" }).click();
  await expect(page).toHaveURL(/layout=matrix/);
  await expect(page.locator(".vmatrix table")).toBeVisible();
  await page.getByRole("tab", { name: "list" }).click();
  await page.getByRole("button", { name: "+ Filter" }).click();
  await page.getByLabel("Filter on").selectOption("state");
  await page.getByLabel("Value").selectOption("open");
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByRole("group", { name: "Filters" })).toContainText("state open");
  await page.reload();
  await expect(page.getByRole("group", { name: "Filters" })).toContainText("state open");
});

test("saving a view lists it in the saved-views menu", async ({ page }) => {
  const name = "e2e view " + Math.random().toString(36).slice(2, 6);
  await page.goto("/w/acme/views");
  await page.getByLabel("View name").fill(name);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByLabel("Saved views").locator("option", { hasText: name })).toHaveCount(1);
});
