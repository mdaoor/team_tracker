import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { makeContext } from "./context.js";
import { drillList } from "./interact.js";
import { RENDERERS } from "./renderers/index.js";

const data = JSON.parse(readFileSync("fixtures/golden.json", "utf8"));
const ctx = makeContext(data);
const text = (html) =>
  new DOMParser().parseFromString(html, "text/html").body.textContent;

describe("UX 14: every KPI opens the list of items behind its number", () => {
  const doc = new DOMParser().parseFromString(RENDERERS.kpis(ctx), "text/html");
  const tiles = Object.fromEntries(
    [...doc.querySelectorAll(".kpi")].map((k) => [
      k.querySelector(".l").textContent,
      {
        value: k.querySelector(".v").textContent,
        spec: JSON.parse(k.dataset.drill),
        focusable: k.tabIndex === 0 && k.getAttribute("role") === "button",
      },
    ]),
  );
  const list = (l) => drillList(ctx, tiles[l].spec);

  test("all eight tiles are focusable buttons with a drill", () => {
    expect(Object.keys(tiles)).toHaveLength(8);
    for (const t of Object.values(tiles)) {
      expect(t.focusable).toBe(true);
      expect(t.spec.f).toBeTruthy();
    }
  });
  test("count tiles list exactly as many items as the number says", () => {
    for (const l of ["Open", "Active", "Done"])
      expect(list(l).items).toHaveLength(Number(tiles[l].value));
  });
  test("the other tiles explain how their number relates to the list", () => {
    expect(list("Hours").items.every((i) => i.hours_logged > 0)).toBe(true);
    expect(tiles.Hours.spec.note).toContain(`${tiles.Hours.value} h logged`);
    const v = list("Violations");
    expect(v.items.length).toBeGreaterThan(0);
    expect(tiles.Violations.spec.note).toBe(
      `${tiles.Violations.value} open violations on ${v.items.length} items`,
    );
    expect(text(v.html)).toContain(tiles.Violations.spec.note);
    expect(list("Risk exposure").items.every((i) => i.risks.length)).toBe(true);
    expect(tiles["On-time"].spec.note).toMatch(
      /^\d+ of \d+ finished items were late$/,
    );
    expect(
      list("On-time").items.every((i) => i.actual_end > i.planned_end),
    ).toBe(true);
    expect(tiles["Weighted throughput"].spec.note).toMatch(
      /over 10 items done$/,
    );
  });
  test("the drill header is escaped", () => {
    const h = drillList(ctx, {
      f: {},
      label: "<img onerror=x>",
      note: "<b>",
    }).html;
    expect(h).not.toContain("<img");
    expect(h).not.toContain("<b>");
  });
});
