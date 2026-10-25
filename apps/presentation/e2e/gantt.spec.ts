import { expect, test } from "@playwright/test";

// Plan 15 R6.4: the Roadmap slide is drawn by the shared Gantt (src/gantt.js): axis, today line, labels, milestones, dependency
// arrows, zoom, rows = phases | epics | milestones. The web console can import the same module (dist/gantt.mjs).

test("the roadmap slide draws bars, dependency arrows, the baseline ghost and a table twin", async ({
  page,
}) => {
  await page.goto("/?src=/plan.json");
  const svg = page.getByRole("img", {
    name: /^Roadmap: 5 rows, 2 dependencies/,
  });
  await expect(svg).toBeVisible();
  await expect(svg.locator("rect.rm-bar")).toHaveCount(5);
  await expect(svg.locator("path.rm-dep")).toHaveCount(2);
  await expect(svg.locator(".rm-ghost, .rm-ghost-bar")).toHaveCount(1);
  expect(
    await svg
      .locator("text.rm-label")
      .evaluateAll((els) => els.map((e) => e.textContent)),
  ).toContain("Design");
  // nothing overflows the slide and every drawn number is finite
  const outer = await page
    .locator(".slide.active .s-body")
    .evaluate((el) => el.scrollHeight - el.clientHeight);
  expect(outer).toBeLessThanOrEqual(1);
  expect(await svg.innerHTML()).not.toMatch(/NaN|undefined/);
  await page.locator(".rm-table summary").click();
  await expect(page.locator(".rm-table tbody tr")).toHaveCount(5);
});

test("page mode: zoom and rows are selects; a pixel zoom scrolls inside a labelled region", async ({
  page,
}) => {
  await page.goto("/?src=/plan.json&mode=page");
  const fig = page.locator("figure.roadmap");
  await expect(fig).toHaveAttribute("data-zoom", "fit");
  await expect(fig.locator(".rm-scroll")).toHaveCount(0);
  const fitWidth = await fig
    .locator("svg")
    .evaluate((el) => el.getBoundingClientRect().width);

  await fig.locator('select[data-ctl="zoom"]').selectOption("day");
  const scroll = page.locator("figure.roadmap .rm-scroll");
  await expect(scroll).toHaveAttribute("aria-label", "Roadmap, scrollable");
  const scrollable = await scroll.evaluate(
    (el) => el.scrollWidth > el.clientWidth,
  );
  expect(scrollable).toBe(true);
  const dayWidth = await scroll
    .locator("svg")
    .evaluate((el) => el.getBoundingClientRect().width);
  expect(dayWidth).toBeGreaterThan(fitWidth);

  await page
    .locator('figure.roadmap select[data-ctl="zoom"]')
    .selectOption("month");
  const monthWidth = await page
    .locator("figure.roadmap .rm-scroll svg")
    .evaluate((el) => el.getBoundingClientRect().width);
  expect(monthWidth).toBeLessThan(dayWidth);

  await page
    .locator('figure.roadmap select[data-ctl="rows"]')
    .selectOption("milestone");
  await expect(page.locator("figure.roadmap")).toHaveAttribute(
    "data-rows",
    "milestone",
  );
});
