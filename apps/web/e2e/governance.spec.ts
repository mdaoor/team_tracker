import { expect, test } from "@playwright/test";

const API = "http://localhost:8010/api/v1/w/acme";

test("a manager creates a template policy, dry-runs it, activates it and waives a hit", async ({
  page,
  request,
}) => {
  await page.goto("/w/acme/policies/new");
  await page.getByRole("listitem").filter({ hasText: "Item stuck in a stage" }).click();
  await page.getByLabel("Key").fill("e2e_stuck");
  await page.getByRole("spinbutton").fill("3");
  // no CEL is needed (or shown) for a template policy
  await expect(page.getByTestId("cel-editor")).not.toBeVisible(); // only admins get a (collapsed) compiled view
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Create policy" }).click();
  await expect(page).toHaveURL(/policies\/e2e_stuck/);
  await expect(page.getByRole("button", { name: "Activate", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Run dry run" }).click();
  await expect(page.getByTestId("fired")).toBeVisible();
  const fired = Number(await page.getByTestId("fired").innerText());
  expect(fired).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Activate", exact: true }).click();
  await expect(page.getByText("Active", { exact: true })).toBeVisible();
  const run = await request.post(`${API}/policies/e2e_stuck:run`, { data: {} });
  expect(run.ok()).toBeTruthy();

  await page.goto("/w/acme/violations?policy=e2e_stuck");
  const rows = page.getByTestId("violation");
  await expect(rows.first()).toBeVisible();
  const before = await rows.count();
  await rows.first().getByRole("button", { name: "Waive" }).click();
  const dlg = page.getByRole("dialog", { name: "Waive violation" });
  await expect(dlg.getByRole("button", { name: "Confirm" })).toBeDisabled();
  await dlg.getByLabel("Reason (required)").fill("accepted risk");
  await dlg.getByRole("button", { name: "Confirm" }).click();
  await expect(rows).toHaveCount(before - 1);
  const waived = (
    await (
      await request.get(`${API}/violations`, { params: { status: "waived", policy: "e2e_stuck" } })
    ).json()
  ).data;
  expect(waived).toHaveLength(1);
  expect(waived[0].waive_reason).toBe("accepted risk");
});

test("the CEL editor shows a type error with the field name", async ({ page }) => {
  await page.goto("/w/acme/policies/new");
  await page.getByRole("button", { name: "Edit as CEL" }).click();
  await page.getByTestId("cel-editor").click();
  await page.keyboard.type("item.nope == 1");
  await expect(page.getByRole("alert").first()).toContainText("nope", { timeout: 1000 + 400 });
});

test("the habit report restricts wellbeing for someone who may not see it", async ({ page }) => {
  await page.goto("/w/acme/habits");
  await expect(page.getByRole("heading", { name: "Habits" })).toBeVisible();
  await page.getByText("Wellbeing", { exact: true }).click();
  await expect(page.getByText("Restricted: no wellbeing habits are visible to you")).toBeVisible();
});
