import { expect, test } from "@playwright/test";
import { API } from "./api";

/** Phase 13 R6.3 (UX §3.3): the setup editors behave as designed, not as flat CRUD tables. */
const unique = () => Math.random().toString(36).slice(2, 7);

test("a stage chip opens an editor with a live mini-pipeline and saves", async ({ page }) => {
  await page.goto("/w/acme/setup/process");
  const chip = page.locator(".stage-chip").first();
  const name = (await chip.evaluate((el) => el.childNodes[0].textContent ?? "")).trim();
  await chip.click();
  const dialog = page.getByRole("dialog", { name: /Edit stage/ });
  await expect(dialog).toBeVisible();
  for (const label of ["Expected days", "WIP limit", "Needs approval", "Category"])
    await expect(dialog.getByLabel(label)).toBeVisible();
  const mini = dialog.getByRole("img", { name: "Mini pipeline preview" });
  const before = await mini.locator("span.on").evaluate((el) => getComputedStyle(el).flexGrow);
  await dialog.getByLabel("Expected days").fill("40");
  await expect(mini.locator("span.on")).toHaveAttribute("title", `${name}: 40 d`);
  const after = await mini.locator("span.on").evaluate((el) => getComputedStyle(el).flexGrow);
  expect(Number(after)).toBeGreaterThan(Number(before));
  await dialog.getByLabel("WIP limit").fill("7");
  await dialog.getByLabel("Needs approval").check();
  await dialog.getByRole("button", { name: "Save stage" }).click();
  await expect(page.getByText("Stage updated")).toBeVisible();
  await expect(page.locator(".stage-chip").first()).toContainText("40 d");
  await expect(page.locator(".stage-chip").first()).toContainText("WIP 7");
  await expect(page.locator(".stage-chip").first()).toContainText("approval");
});

test("milestones and iterations show on a month calendar", async ({ page, request }) => {
  const name = `ms-${unique()}`;
  const due = new Date().toISOString().slice(0, 8) + "15";
  const r = await request.post(`${API}/projects/WEB/milestones`, {
    data: { name, due_date: due },
  });
  expect(r.ok()).toBeTruthy();
  await page.goto("/w/acme/setup/planning");
  const cal = page.getByRole("group", { name: /Milestones and iterations: / });
  await expect(cal.locator(`[data-day="${due}"] .ev.milestone`)).toContainText(name);
  const title = await cal.getAttribute("aria-label");
  await page.getByRole("button", { name: "Next month" }).click();
  await expect(cal).not.toHaveAttribute("aria-label", title!);
  await page.getByRole("button", { name: "Today" }).click();
  await expect(cal).toHaveAttribute("aria-label", title!);
});

test("the risk heat map is labelled and a cell filters the register", async ({ page, request }) => {
  const a = `heat-a-${unique()}`;
  const b = `heat-b-${unique()}`;
  for (const [title, probability, impact] of [
    [a, 2, 3],
    [b, 5, 5],
  ] as const) {
    const r = await request.post(`${API}/risks`, {
      data: { project: "WEB", title, probability, impact },
    });
    expect(r.ok()).toBeTruthy();
  }
  await page.goto("/w/acme/setup/risks");
  const map = page.getByRole("table", { name: "Risk heat map" });
  await expect(map.getByText("Probability →")).toBeVisible();
  await expect(map.getByRole("columnheader", { name: /^P3/ })).toBeVisible();
  await expect(map.getByRole("rowheader", { name: /^I5/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: a })).toBeVisible();
  await expect(page.getByRole("cell", { name: b })).toBeVisible();
  await map.getByRole("button", { name: /^probability 2 impact 3: [1-9]/ }).click();
  await expect(page.getByRole("heading", { name: "Risks at P2 × I3" })).toBeVisible();
  await expect(page.getByRole("cell", { name: a })).toBeVisible();
  await expect(page.getByRole("cell", { name: b })).toHaveCount(0);
  await page.getByRole("button", { name: "Show all risks" }).click();
  await expect(page.getByRole("cell", { name: b })).toBeVisible();
});

test("the people screen draws the org tree with team capacity", async ({ page }) => {
  await page.goto("/w/acme/setup/people");
  const tree = page.getByRole("tree", { name: "Reporting lines" });
  await expect(tree).toBeVisible();
  await expect(tree.getByRole("treeitem").first()).toContainText(/h\/wk/);
  await expect(tree).toContainText(/team \d+ h\/wk/);
});

test("pasting a list adds many dimension values at once", async ({ page }) => {
  const key = `d${unique()}`;
  await page.goto("/w/acme/setup/dimensions");
  const form = page.getByRole("form", { name: "Add dimension" });
  await form.getByLabel("Key").fill(key);
  await form.getByLabel("Name").fill(`Dim ${key}`);
  await form.getByRole("button", { name: "Add dimension" }).click();
  const paste = page.getByLabel(`Paste values for ${key}`);
  await paste.fill("alpha, beta\ngamma\n\nalpha");
  await expect(page.getByText("3 values to add")).toBeVisible();
  await paste.locator("xpath=following-sibling::button").click();
  await expect(page.getByText(`Added 3 value(s) to ${key}`)).toBeVisible();
  await expect(page.locator(".card", { hasText: `(${key})` })).toContainText(
    "alpha · beta · gamma",
  );
});
