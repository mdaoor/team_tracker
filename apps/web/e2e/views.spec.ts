import { expect, test } from "@playwright/test";
import { API } from "./api";

test("the Gantt draws a time axis, a today line and milestone diamonds (shared module)", async ({
  page,
  request,
}) => {
  const name = `gantt-ms-${Math.random().toString(36).slice(2, 6)}`;
  const due = new Date(Date.now() + 5 * 86_400_000).toISOString().slice(0, 10);
  const r = await request.post(`${API}/projects/WEB/milestones`, {
    data: { name, due_date: due },
  });
  expect(r.ok()).toBeTruthy();
  await page.goto("/w/acme/views?layout=gantt");
  const region = page.getByRole("region", { name: "Gantt chart" });
  await expect(region.locator("svg.rm-svg")).toBeVisible();
  expect(await region.locator(".rm-month").count()).toBeGreaterThan(3); // time axis ticks
  await expect(region.locator(".rm-today")).toHaveCount(1);
  await expect(region.locator(".rm-todaylbl")).toHaveText("today");
  await expect(region.locator(".rm-ms title", { hasText: name })).toHaveCount(1);
  expect(await region.locator(".rm-bar").count()).toBeGreaterThan(0);
});

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
  await page.getByRole("button", { name: "Add", exact: true }).click();
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
