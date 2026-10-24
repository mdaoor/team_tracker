import { expect, test, type APIRequestContext } from "@playwright/test";

const W = "/api/v1/w/acme";
const unique = () => Math.random().toString(36).slice(2, 6).toUpperCase();

async function capture(request: APIRequestContext, line: object) {
  const r = await request.post(`http://localhost:8010${W}/capture/batches`, {
    data: {
      workspace: "acme",
      messages: [
        {
          actor: "@mahmoud",
          at: new Date().toISOString(),
          client_id: crypto.randomUUID(),
          ...line,
        },
      ],
    },
  });
  expect(r.ok()).toBeTruthy();
}

test("a project needs only a name, a key and a process", async ({ page }) => {
  const key = "E" + unique();
  await page.goto("/w/acme/setup/projects");
  const form = page.getByRole("form", { name: "Add project" });
  await form.getByLabel("Name").fill("E2E project");
  await form.getByLabel("Key").fill(key);
  await form.getByLabel("Process").selectOption({ index: 1 });
  await form.getByRole("button", { name: "Add project" }).click();
  await expect(page.getByRole("cell", { name: key, exact: true })).toBeVisible();
});

test("reordering stages updates the pipeline", async ({ page }) => {
  await page.goto("/w/acme/setup/process");
  const stages = page.getByRole("list", { name: "Stages" }).locator("li");
  const first = await stages.first().getAttribute("data-stage");
  await page
    .getByRole("button", { name: /Move .* right/ })
    .first()
    .click();
  await expect(stages.nth(1)).toHaveAttribute("data-stage", first!);
  await page.reload();
  await expect(stages.nth(1)).toHaveAttribute("data-stage", first!);
});

test("resolving a review row teaches the glossary", async ({ page, request }) => {
  const phrase = "the thing " + unique().toLowerCase();
  await capture(request, { action: "create", item: { title: "fix " + phrase }, project: phrase });
  await page.goto("/w/acme/review");
  const row = page.getByRole("option", { name: new RegExp(phrase) });
  await expect(row).toBeVisible();
  await row.getByLabel("other project").selectOption({ index: 1 });
  await expect(row).toHaveCount(0);
  await page.goto("/w/acme/setup/glossary");
  await expect(page.getByRole("cell", { name: phrase, exact: true })).toBeVisible();
});

test("review queue works by keyboard alone", async ({ page, request }) => {
  const a = "kb " + unique().toLowerCase();
  await capture(request, { action: "create", item: { title: a }, project: a });
  await page.goto("/w/acme/review");
  await expect(
    page.getByRole("listbox", { name: "Review rows" }).getByRole("option").first(),
  ).toBeVisible();
  await page.keyboard.press("j");
  await page.keyboard.press("k");
  await page.keyboard.press("x");
  await expect(page.getByRole("checkbox").first()).toBeChecked();
});

test("a conflicting edit opens the conflict dialog and 'keep mine' wins", async ({
  browser,
  page,
}) => {
  const key = (
    await (
      await page.request.get(`http://localhost:8010${W}/items`, { params: { limit: 1 } })
    ).json()
  ).data[0].key;
  const other = await browser.newPage({ extraHTTPHeaders: { "X-Debug-Actor": "@mahmoud" } });
  await page.goto(`/w/acme/review?item=${key}`);
  await expect(page.getByLabel("Priority")).toBeVisible();
  await other.goto(`/w/acme/review?item=${key}`);
  await other.getByLabel("Priority").selectOption("critical");
  await expect(other.getByText(/priority → critical/)).toBeVisible();
  await page.getByLabel("Priority").selectOption("low");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("theirs")).toHaveText("critical");
  await dialog.getByText("Keep mine").click();
  await expect(page.getByText(/priority → low/)).toBeVisible();
  await other.close();
});

test("dragging a risk onto an item links it", async ({ page, request }) => {
  const r = await request.post(`http://localhost:8010${W}/risks`, {
    data: { project: "WEB", title: "e2e vendor risk", probability: 4, impact: 4 },
  });
  expect(r.ok()).toBeTruthy();
  await page.goto("/w/acme/setup/risks");
  const chip = page.locator("tr", { hasText: "e2e vendor risk" }).locator("span.chip");
  const target = page.locator(".droplist li").first();
  await chip.dragTo(target);
  await expect(page.getByText(/Linked .* children inherit it/)).toBeVisible();
});
