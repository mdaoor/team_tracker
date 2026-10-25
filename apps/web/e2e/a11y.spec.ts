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
    console.log(`a11y ${name}: ${bad.map((v) => `${v.id}(${v.impact}) x${v.nodes.length}`).join("; ") || "none"}`);
    expect(bad.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 120)}`)).toEqual([]);
    expect(bad.map((v) => v.id)).not.toContain("page-has-heading-one");
  });
}

test("axe: review queue with rows", async ({ page, request }) => {
  await request.post("/api/v1/w/acme/capture/batches", {
    data: {
      workspace: "acme",
      messages: [
        { actor: "@mahmoud", action: "create", at: new Date().toISOString(), client_id: `a11y-${Date.now()}`, item: { title: "tune the retry" }, project: "the retry thing" },
      ],
    },
  });
  await page.goto("/w/acme/review");
  await page.waitForSelector(".rq-row");
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  console.log(`a11y review rows: ${res.violations.map((v) => `${v.id}(${v.impact}) x${v.nodes.length}`).join("; ") || "none"}`);
  expect(res.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
});
