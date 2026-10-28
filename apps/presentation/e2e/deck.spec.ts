import { expect, test } from "@playwright/test";

const SECTIONS = [
  "Overview",
  "Projects & epics",
  "Delivery pipeline",
  "Dimension explorer",
  "Delivery radar",
  "Gantt",
  "Now",
  "Team activity",
  "Violations & habits",
];
const open = (page: import("@playwright/test").Page, qs = "") =>
  page.goto(`/?src=/golden.json${qs}`);
const title = (page: import("@playwright/test").Page) =>
  page.locator(".slide.active .s-title");

test("the Portfolio review template is the prototype's nine sections, in order, one per screen", async ({
  page,
}) => {
  await open(page);
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  for (let n = 0; n < SECTIONS.length; n++) {
    await expect(title(page)).toHaveText(SECTIONS[n]);
    await expect(page.locator(".counter")).toHaveText(`${n + 1} / 9`);
    if (n < 8) await page.keyboard.press("ArrowRight");
  }
  await page.keyboard.press("ArrowLeft");
  await expect(title(page)).toHaveText("Team activity");
  await page.keyboard.press("Home");
  await expect(title(page)).toHaveText("Overview");
  await page.keyboard.press("End");
  await expect(title(page)).toHaveText("Violations & habits");
  await page.keyboard.press("PageUp");
  await page.keyboard.press("Space");
  await expect(title(page)).toHaveText("Violations & habits");
});

test("G shows nine thumbnails and ?slide=3 deep-links to the pipeline", async ({
  page,
}) => {
  await open(page, "&slide=3");
  await expect(title(page)).toHaveText("Delivery pipeline");
  await page.keyboard.press("g");
  await expect(page.locator(".grid-overview .thumb")).toHaveCount(9);
  await page.locator(".grid-overview .thumb").nth(4).click();
  await expect(title(page)).toHaveText("Delivery radar");
  await expect(page).toHaveURL(/slide=5/);
});

test("clicking the test column drills inside the deck; a row opens the drawer; Esc unwinds", async ({
  page,
}) => {
  await open(page, "&slide=3");
  await page.locator('.col[data-stage="test"]').click();
  const overlay = page.getByRole("dialog", { name: "Drill-down" });
  await expect(overlay).toBeVisible();
  const rows = overlay.locator("tr.row");
  expect(await rows.count()).toBeGreaterThan(0);
  await rows.first().click();
  await expect(page.getByRole("dialog", { name: "Item details" })).toHaveClass(
    /open/,
  );
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "Item details" }),
  ).not.toHaveClass(/open/);
  await expect(overlay).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(overlay).toBeHidden();
});

test("keyboard-only: tab to a KPI, Enter drills, tab to a row, Enter opens its drawer", async ({
  page,
}) => {
  await open(page);
  await page.locator(".kpi").first().focus();
  await page.keyboard.press("Enter");
  const overlay = page.getByRole("dialog", { name: "Drill-down" });
  await expect(overlay).toBeVisible();
  await overlay.locator("tr.row").first().focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Item details" })).toHaveClass(
    /open/,
  );
});

test("the gantt chart has a table toggle", async ({ page }) => {
  await open(page, "&slide=6");
  await expect(page.locator(".slide.active svg")).toBeVisible();
  await page.locator(".tbl-toggle").click();
  await expect(page.locator(".slide.active svg")).toHaveCount(0);
  await expect(page.locator(".slide.active table")).toBeVisible();
});

test("the pipeline and radar slides have a table toggle that keeps the numbers and the drill", async ({
  page,
}) => {
  await open(page, "&slide=3");
  await expect(page.locator(".slide.active .pipe .col").first()).toBeVisible();
  const total = async () =>
    (
      await page.locator(".slide.active .pipe .col .n").allTextContents()
    ).reduce((n, t) => n + Number(t), 0);
  const sum = await total();
  await page.locator(".slide.active .tbl-toggle").click();
  await expect(page.locator(".slide.active .pipe")).toHaveCount(0);
  const counts = await page
    .locator(".slide.active tbody tr td:nth-child(2)")
    .allTextContents();
  expect(counts.reduce((n, t) => n + Number(t), 0)).toBe(sum);
  await page.locator(".slide.active tbody a").first().click();
  await expect(page.getByRole("dialog", { name: "Drill-down" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.locator(".slide.active .tbl-toggle").click();
  await expect(page.locator(".slide.active .pipe .col").first()).toBeVisible();

  await open(page, "&slide=5");
  await expect(page.locator(".slide.active .tbl-toggle")).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await page.locator(".slide.active .tbl-toggle").click();
  await expect(page.locator(".slide.active thead th").first()).toHaveText(
    "due",
  );
  await expect(
    page.locator(".slide.active tr[data-open]").first(),
  ).toBeVisible();
});

test("the gantt slide is the shared one: epic rows, today line, milestone diamonds", async ({
  page,
}) => {
  await open(page, "&slide=6");
  const svg = page.locator(".slide.active svg.rm-svg");
  await expect(svg).toBeVisible();
  expect(await svg.locator("rect.rm-bar.epic").count()).toBeGreaterThan(0);
  await expect(svg.locator(".rm-today")).toHaveCount(1);
  expect(await svg.locator(".rm-ms").count()).toBeGreaterThan(0);
});

test("notes (N) and reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await open(page);
  await page.keyboard.press("n");
  await expect(page.locator(".notes")).toContainText("Notes for Overview");
  const d = await page
    .locator(".slide.active")
    .evaluate((el) => getComputedStyle(el).transitionDuration);
  expect(d).toBe("0s");
});

test("no request leaves the origin; the data comes from one same-origin fetch", async ({
  page,
}) => {
  const urls: string[] = [];
  page.on("request", (r) => urls.push(r.url()));
  await open(page);
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  expect(
    urls.filter(
      (u) => !u.startsWith("http://localhost:4173") && !u.startsWith("data:"),
    ),
  ).toEqual([]);
});

test("print mode: one A4-landscape page per slide, each stamped", async ({
  page,
}) => {
  await open(page, "&mode=print");
  await expect(page.locator(".slide")).toHaveCount(9);
  await expect(page.locator(".slide .stamp")).toHaveCount(9);
  const pdf = await page.pdf({
    format: "A4",
    landscape: true,
    printBackground: true,
    preferCSSPageSize: true,
  });
  const pages = (pdf.toString("latin1").match(/\/Type\s*\/Page[^s]/g) ?? [])
    .length;
  expect(pages).toBe(9);
});

test("page mode stacks the same sections and keeps the drill-down breadcrumb", async ({
  page,
}) => {
  await open(page, "&mode=page");
  await expect(page.locator("section[data-slide]")).toHaveCount(9);
  await page.locator('.col[data-stage="test"]').click();
  await expect(page.locator("#crumbs .chip.active")).toHaveText("stage test");
  await expect(page.locator("#drill tr.row").first()).toBeVisible();
  await page.locator('[data-pop="0"]').click();
  await expect(page.locator("#drill tr.row")).toHaveCount(0);
});

test("a cross-origin ?src= is refused before any request is made (review 18)", async ({
  page,
}) => {
  const calls: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).hostname === "evil.example") calls.push(r.url());
  });
  await page.goto("/?src=https://evil.example/deck.json");
  await expect(page.locator("html")).toHaveAttribute("data-ready", "error");
  await expect(page.locator("body")).toContainText("same origin");
  expect(calls).toEqual([]);
});
