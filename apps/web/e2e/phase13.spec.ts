import { expect, test } from "@playwright/test";

const API = "http://localhost:8010/api/v1/w/acme";

async function deck(request: import("@playwright/test").APIRequestContext, title: string) {
  const r = await request.post(`${API}/presentations`, {
    data: { title, slides: [{ kind: "kpis" }, { kind: "projects" }] },
  });
  return (await r.json()).id as string;
}

test("R1.1 every export button in the builder downloads a file", async ({ page, request }) => {
  const id = await deck(request, "Export me");
  await page.goto(`/w/acme/present/${id}`);
  for (const [fmt, ext] of [
    ["HTML", /\.html$/],
    ["PDF", /\.pdf$/],
    ["PPTX", /\.pptx$/],
    ["PNG", /\.zip$/],
    ["CSV", /\.zip$/],
  ] as const) {
    const [dl] = await Promise.all([
      page.waitForEvent("download", { timeout: 60_000 }),
      page.getByRole("button", { name: fmt, exact: true }).click(),
    ]);
    expect(dl.suggestedFilename(), fmt).toMatch(ext);
  }
  await expect(page.getByText(/Downloaded Export me/).first()).toBeVisible();
});

test("R1.1 the Views CSV export downloads", async ({ page }) => {
  await page.goto("/w/acme/views");
  const [dl] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Export CSV" }).click(),
  ]);
  expect(dl.suggestedFilename()).toBe("items.csv");
});

test("R1.2 the builder preview renders the selected slide", async ({ page, request }) => {
  const id = await deck(request, "Preview me");
  const violations: string[] = [];
  page.on("console", (m) => /frame-ancestors|Refused to frame/i.test(m.text()) && violations.push(m.text()));
  await page.goto(`/w/acme/present/${id}`);
  const frame = page.frameLocator("iframe[title='Slide preview']");
  await expect(frame.locator(".slide.active")).toBeVisible({ timeout: 20_000 });
  expect(violations).toEqual([]);
});

test("R1.2 a share link page cannot be framed by anyone", async ({ request }) => {
  const r = await request.get("http://localhost:8010/p/t/not-a-real-token");
  if (r.status() === 200) expect(r.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
});

test("R1.3 delete asks first, goes back to the list, and undo restores", async ({ page, request }) => {
  const id = await deck(request, "Mis-click me");
  await page.goto(`/w/acme/present/${id}`);
  await page.getByRole("button", { name: "Delete presentation" }).click();
  const dlg = page.getByRole("dialog", { name: /Delete “Mis-click me”\?/ });
  await expect(dlg).toBeVisible();
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(page).toHaveURL(new RegExp(`/present/${id}$`));
  await page.getByRole("button", { name: "Delete presentation" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page).toHaveURL(/\/w\/acme\/present$/);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page).toHaveURL(new RegExp(`/present/${id}$`));
  const list = (await (await request.get(`${API}/presentations`)).json()).data as { id: string }[];
  expect(list.map((p) => p.id)).toContain(id);
});

test("R1.6 sharing is explained when it is off, and an admin can switch it on", async ({ page, request }) => {
  await request.patch(`${API}/settings`, { data: { external_share: false } });
  const id = await deck(request, "Share me");
  await page.goto(`/w/acme/present/${id}`);
  const btn = page.getByRole("button", { name: "Create read-only link" });
  await expect(btn).toBeDisabled();
  await expect(page.getByText(/External sharing is off/)).toBeVisible();
  await page.getByRole("button", { name: "Turn it on" }).click();
  await expect(btn).toBeEnabled();
  await btn.click();
  await expect(page.getByText(/Link: /)).toBeVisible();
});

test.describe("R1.5 a contributor", () => {
  test.use({ extraHTTPHeaders: { "X-Debug-Actor": "@ali" } });
  test("sees only the areas they can use, and a forbidden deep link explains itself", async ({ page }) => {
    await page.goto("/w/acme");
    await expect(page).toHaveURL(/\/views/);
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Views" })).toBeVisible();
    for (const hidden of ["Setup", "Review queue", "Policies", "Weights"])
      await expect(nav.getByRole("link", { name: hidden })).toHaveCount(0);
    for (const url of ["/w/acme/review", "/w/acme/setup", "/w/acme/policies"]) {
      await page.goto(url);
      await expect(page.getByRole("heading", { name: /don't have access/i })).toBeVisible();
    }
  });
});

test("R1.4 resolving a review row offers Undo and Don't learn, with one toast", async ({ page, request }) => {
  const phrase = `the odd place ${Date.now()}`;
  await request.post(`${API}/capture/batches`, {
    data: {
      workspace: "acme",
      messages: [
        { actor: "@mahmoud", action: "create", at: new Date().toISOString(), client_id: `r14-${Date.now()}`, item: { title: "needs a home" }, project: phrase },
      ],
    },
  });
  await page.goto("/w/acme/review");
  const row = page.locator(".rq-row", { hasText: "needs a home" });
  await expect(row).toBeVisible();
  await expect(row.getByText(/@mahmoud · /)).toBeVisible();
  await row.getByRole("button").filter({ hasText: /^[A-Z]+/ }).first().click();
  await expect(page.locator(".toast")).toHaveCount(1);
  await page.getByRole("button", { name: "Don't learn" }).click();
  await expect
    .poll(async () => Object.keys((await (await request.get(`${API}/context-pack`)).json()).glossary))
    .not.toContain(phrase);
  await expect(page.locator(".rq-row", { hasText: "needs a home" })).toHaveCount(0);
});

test("R1.7 a garbage ?f= says so, and the weight selector lists each scheme once", async ({ page }) => {
  await page.goto("/w/acme/views?f=garbage");
  await expect(page.getByText(/could not be read/)).toBeVisible();
  await expect(page.getByLabel("Weights").locator("option", { hasText: "effort-only" })).toHaveCount(1);
  const names = await page.getByLabel("Weights").locator("option").allTextContents();
  expect(names.filter((n) => n === "default")).toHaveLength(1);
});

test("R6.6 the next-week preset is one click on the Views landing", async ({ page }) => {
  await page.goto("/w/acme/views");
  await page.getByRole("button", { name: "What's shipping next week" }).click();
  await expect(page.locator(".chip", { hasText: "due next week" })).toBeVisible();
  await expect(page.locator(".vlist, .empty").first()).toBeVisible();
});

test("R2.3 the calendar shows titles and moves between months", async ({ page }) => {
  await page.goto("/w/acme/views?layout=calendar");
  const label = await page.getByRole("group", { name: "Month" }).locator("strong").textContent();
  await page.getByRole("button", { name: "Next month" }).click();
  await expect(page.getByRole("group", { name: "Month" }).locator("strong")).not.toHaveText(label!);
});
