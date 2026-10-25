import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/** Accessibility audit (plan P5/P9 acceptance: axe score >= 95). Score = share of rule checks that pass; and no serious/critical
 * violation may remain on any primary screen. A known, accepted gap goes in KNOWN with a reason, never silently. */
const SCREENS: [string, string][] = [
  ["home", "/w/acme"],
  ["review queue", "/w/acme/review"],
  ["views", "/w/acme/views"],
  ["present", "/w/acme/present"],
  ["templates", "/w/acme/present/templates"],
  ["policies", "/w/acme/policies"],
  ["new policy", "/w/acme/policies/new"],
  ["violations", "/w/acme/violations"],
  ["habits", "/w/acme/habits"],
  ["performance", "/w/acme/performance"],
  ["digest", "/w/acme/digest"],
  ["insights", "/w/acme/insights"],
  ["weights", "/w/acme/weights"],
  ["setup", "/w/acme/setup"],
];
const KNOWN: Record<string, string> = {};

for (const [name, url] of SCREENS) {
  test(`axe: ${name}`, async ({ page }) => {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    const res = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const bad = res.violations.filter((v) => !(v.id in KNOWN));
    const severe = bad.filter((v) => v.impact === "serious" || v.impact === "critical");
    const total = res.passes.length + res.violations.length;
    const score = total ? Math.round((res.passes.length / total) * 100) : 100;
    console.log(
      `a11y ${name}: score ${score}, violations ${bad.map((v) => `${v.id}(${v.impact}) x${v.nodes.length}`).join("; ") || "none"}`,
    );
    expect(severe.map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 120)}`)).toEqual([]);
    expect(score).toBeGreaterThanOrEqual(95);
  });
}

/** Layouts with data (UX review a11y table): the default screens above are empty enough to pass, these are not. */
const LAYOUT_SCREENS: [string, string][] = [
  ["views board", "/w/acme/views?layout=board"],
  ["views pipeline", "/w/acme/views?layout=pipeline"],
  ["views matrix", "/w/acme/views?layout=matrix"],
  ["views gantt", "/w/acme/views?layout=gantt"],
  ["views calendar", "/w/acme/views?layout=calendar"],
  ["views now", "/w/acme/views?layout=now"],
];
for (const [name, url] of LAYOUT_SCREENS) {
  test(`axe: ${name}`, async ({ page }) => {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(400);
    const res = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const bad = res.violations;
    console.log(
      `a11y ${name}: ${bad.map((v) => `${v.id}(${v.impact}) x${v.nodes.length}`).join("; ") || "none"}`,
    );
    expect(
      bad
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 120)}`),
    ).toEqual([]);
    expect(bad.map((v) => v.id)).not.toContain("page-has-heading-one");
  });
}

test("axe: review queue with rows", async ({ page, request }) => {
  await request.post("/api/v1/w/acme/capture/batches", {
    data: {
      workspace: "acme",
      messages: [
        {
          actor: "@mahmoud",
          action: "create",
          at: new Date().toISOString(),
          client_id: `a11y-${Date.now()}`,
          item: { title: "tune the retry" },
          project: "the retry thing",
        },
      ],
    },
  });
  await page.goto("/w/acme/review");
  await page.waitForSelector(".rq-row");
  const res = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  console.log(
    `a11y review rows: ${res.violations.map((v) => `${v.id}(${v.impact}) x${v.nodes.length}`).join("; ") || "none"}`,
  );
  expect(
    res.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => v.id),
  ).toEqual([]);
});

/** Phase 13 R5.3: at 390x844 no console screen scrolls sideways (document scrollWidth <= innerWidth), none has a
 * serious/critical axe violation, and navigation is a drawer. Covers the 14 screens above plus the data layouts. */
const SETUP_SECTIONS: [string, string][] = [
  "projects",
  "process",
  "dimensions",
  "people",
  "planning",
  "risks",
].map((s) => [`setup ${s}`, `/w/acme/setup/${s}`]);
const MOBILE: [string, string][] = [...SCREENS, ...LAYOUT_SCREENS, ...SETUP_SECTIONS];
test.describe("mobile 390x844", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  for (const [name, url] of MOBILE) {
    test(`mobile: ${name}`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(300);
      const over = await page.evaluate(() => {
        const de = document.documentElement;
        return { scrollWidth: de.scrollWidth, innerWidth: window.innerWidth };
      });
      expect(over.scrollWidth, JSON.stringify(over)).toBeLessThanOrEqual(over.innerWidth);
      const res = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(
        res.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 120)}`),
      ).toEqual([]);
    });
  }
  test("the menu button opens the navigation drawer", async ({ page }) => {
    await page.goto("/w/acme/views");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav).not.toBeInViewport();
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(nav).toBeInViewport();
    await nav.getByRole("link", { name: "Present" }).click();
    await expect(page).toHaveURL(/\/present/);
    await expect(nav).not.toBeInViewport();
  });
});
