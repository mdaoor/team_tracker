import { expect, test, type Page } from "@playwright/test";

// Phase 13 R6.5 (deck auto-fit) and UX issue 14 (KPI numbers drill) in a real browser.

const deck = (page: Page, src: string, qs = "") =>
  page.goto(`/?src=${src}${qs}`);
const active = (page: Page) => page.locator(".slide.active");
const overflow = (page: Page) =>
  active(page)
    .locator(".s-body")
    .evaluate((el) => el.scrollHeight - el.clientHeight);

test("clicking a KPI lists the items behind its number, and says how the number relates to them", async ({
  page,
}) => {
  await deck(page, "/golden.json");
  const overlay = page.getByRole("dialog", { name: "Drill-down" });

  await page.locator(".kpi", { hasText: "Done" }).first().click();
  await expect(overlay).toBeVisible();
  const done = Number(
    await page
      .locator(".kpi", { hasText: "Done" })
      .first()
      .locator(".v")
      .innerText(),
  );
  await expect(overlay.locator("tr.row")).toHaveCount(done);
  await expect(overlay.locator("h2")).toContainText(`${done} · done`);
  await page.keyboard.press("Escape");
  await expect(overlay).toBeHidden();

  await page.locator(".kpi", { hasText: "Violations" }).click();
  await expect(overlay).toBeVisible();
  const n = await overlay.locator("tr.row").count();
  const total = await page
    .locator(".kpi", { hasText: "Violations" })
    .locator(".v")
    .innerText();
  await expect(overlay.locator(".drill-note")).toHaveText(
    `${total} open violations on ${n} items`,
  );
  await page.keyboard.press("Escape");
  await expect(overlay).toBeHidden();
});

test("every KPI tile is reachable by keyboard and opens a non-empty list", async ({
  page,
}) => {
  await deck(page, "/golden.json");
  const overlay = page.getByRole("dialog", { name: "Drill-down" });
  const tiles = page.locator(".kpi");
  for (let i = 0; i < (await tiles.count()); i++) {
    await tiles.nth(i).focus();
    await page.keyboard.press("Enter");
    await expect(overlay).toBeVisible();
    expect(await overlay.locator("tr.row").count()).toBeGreaterThan(0);
    await page.keyboard.press("Escape");
    await expect(overlay).toBeHidden();
  }
});

test("page mode: a KPI drill keeps its note under the breadcrumb", async ({
  page,
}) => {
  await deck(page, "/golden.json", "&mode=page");
  await page.locator(".kpi", { hasText: "Risk exposure" }).click();
  await expect(page.locator("#drill .drill-note")).toContainText("exposure");
  await expect(page.locator("#crumbs .chip.active")).toHaveText("with risk");
});

test("fit: paginate continues a long Projects slide as '(cont.)' and nothing overflows", async ({
  page,
}) => {
  await deck(page, "/many.json?n=9&fit=paginate");
  await expect(page.locator(".counter")).toHaveText("1 / 11");
  await page.goto("/?src=/many.json?n=9%26fit=paginate&slide=2");
  await expect(page.locator(".slide.active .s-title")).toHaveText(
    "Projects & epics",
  );
  expect(await overflow(page)).toBeLessThanOrEqual(1);
  await page.keyboard.press("ArrowRight");
  await expect(page.locator(".slide.active .s-title")).toHaveText(
    "Projects & epics (cont.)",
  );
  await page.keyboard.press("ArrowRight");
  await expect(page.locator(".slide.active .s-title")).toHaveText(
    "Projects & epics (cont.)",
  );
  expect(await overflow(page)).toBeLessThanOrEqual(1);
  // every project appears exactly once across the pages
  await page.goto("/?src=/many.json?n=9%26fit=paginate&mode=print");
  await expect(
    page.locator('[data-kind="projects"] .card[data-project]'),
  ).toHaveCount(9);
});

test("fit: shrink keeps one slide, scaled, with every project on it", async ({
  page,
}) => {
  await deck(page, "/many.json?n=9%26fit=shrink", "&slide=2");
  await expect(page.locator(".counter")).toHaveText("2 / 9");
  await expect(active(page).locator(".s-title")).toHaveText("Projects & epics");
  await expect(active(page).locator(".s-body")).toHaveAttribute(
    "data-shrink",
    "1",
  );
  await expect(active(page).locator(".card[data-project]")).toHaveCount(9);
  await page.keyboard.press("ArrowRight");
  await expect(active(page).locator(".s-title")).not.toContainText("(cont.)");
});
