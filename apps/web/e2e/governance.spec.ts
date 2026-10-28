import { expect, test } from "@playwright/test";
import { API } from "./api";

test("a manager creates a template policy, dry-runs it, activates it and waives a hit", async ({
  page,
  request,
}) => {
  await page.goto("/w/acme/policies/new");
  await page
    .getByRole("group", { name: "Templates" })
    .getByRole("button", { name: /Item stuck in a stage/ })
    .click();
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

test("the thin editors: scope, filter, notify, automatic action are saved with the policy and shown in version history", async ({
  page,
  request,
}) => {
  await page.goto("/w/acme/policies/new");
  await page
    .getByRole("group", { name: "Templates" })
    .getByRole("button", { name: /Item stuck in a stage/ })
    .click();
  await page.getByLabel("Key").fill("e2e_editors");
  await page.getByRole("spinbutton").fill("5");
  await page.getByLabel("Policy scope type").selectOption("project");
  await page.getByLabel("Policy scope", { exact: true }).selectOption({ index: 1 });
  await page.getByRole("group", { name: "Their managers" }).getByLabel("senior manager").check();
  await page.getByLabel("Raise priority to").selectOption("high");
  await page.getByRole("button", { name: "Create policy" }).click();
  await expect(page).toHaveURL(/policies\/e2e_editors/);
  const p = await (await request.get(`${API}/policies/e2e_editors`)).json();
  expect(p.scope_type).toBe("project");
  expect(p.scope_id).toBeTruthy();
  expect(p.notify.reporting_line).toContain("senior");
  expect(p.auto_action).toEqual({ set: { priority: "high" } });
  // the detail form loads the saved policy after the redirect: type only once it has, or the load would overwrite the edit
  const grace = page.getByLabel("Grace period (e.g. 2d)");
  await expect(grace).toBeVisible();
  await page.waitForLoadState("networkidle");
  await grace.fill("2d");
  await expect(grace).toHaveValue("2d");
  await page.getByRole("button", { name: "Save changes" }).click();
  const history = page.getByRole("list", { name: "Version history" });
  await expect(history.getByRole("listitem")).toHaveCount(2);
  await expect(history).toContainText("grace period");
  await page.getByRole("button", { name: "Restore this version" }).click();
  await expect(page.getByText(/Restored/)).toBeVisible();
});

test("typing in the policy form is not overwritten when the saved policy or the templates arrive late", async ({
  page,
  request,
}) => {
  const key = `e2e_late_${Math.random().toString(36).slice(2, 6)}`;
  const made = await request.post(`${API}/policies`, {
    data: { key, name: "Late templates", template: "stuck_in_stage", params: { days: 5 } },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  // the form used to render empty and fill itself in when the policy (and later the template list) arrived, overwriting the typing
  const slow = (ms: number) => async (route: import("@playwright/test").Route) => {
    await new Promise((r) => setTimeout(r, ms));
    await route.continue();
  };
  await page.route(`**/policies/${key}`, slow(1200));
  await page.route("**/policy-templates", slow(1500));
  await page.goto(`/w/acme/policies/${key}`);
  const grace = page.getByLabel("Grace period (e.g. 2d)");
  await expect(grace).toBeVisible();
  await grace.fill("4d");
  await page.waitForTimeout(2500);
  await expect(grace).toHaveValue("4d");
});

test("every governance report can be added to a presentation", async ({ page }) => {
  for (const path of ["violations", "habits", "performance", "insights", "digest", "weights"]) {
    await page.goto(`/w/acme/${path}`);
    await page.getByRole("button", { name: "Add to presentation" }).click();
    await page.getByRole("menuitem", { name: /^＋ New presentation/ }).click();
    await expect(page.getByText("Added to presentation")).toBeVisible();
  }
});

test("habit rows carry a sparkline whose label carries the numbers", async ({ page }) => {
  await page.goto("/w/acme/habits");
  const spark = page.getByRole("img", { name: /last 8 weeks/ }).first();
  await expect(spark).toBeVisible();
  await expect(spark).toHaveAttribute("aria-label", /\d\.\d\d|n\/a/);
});
