import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { makeContext } from "./context.js";
import { md, renderImage } from "./renderers/misc.js";
import { RENDERERS } from "./renderers/index.js";
import { expandSlides } from "./template.js";
import { applyFilter, measureOf, violationsOf } from "./util.js";

const data = JSON.parse(readFileSync("fixtures/golden.json", "utf8"));
const ctx = makeContext(data);
const text = (html) =>
  new DOMParser().parseFromString(html, "text/html").body.textContent;

describe("sections draw the prototype's numbers from the golden snapshot", () => {
  test("KPI tiles", () => {
    const tiles = [
      ...new DOMParser()
        .parseFromString(RENDERERS.kpis(ctx), "text/html")
        .querySelectorAll(".kpi"),
    ].map((k) => [
      k.querySelector(".l").textContent,
      k.querySelector(".v").textContent,
    ]);
    expect(Object.fromEntries(tiles)).toMatchObject({
      Open: "8",
      Active: "24",
      Done: "10",
      Violations: "36",
      "Risk exposure": "32",
      "On-time": "50%",
    });
  });
  test("pipeline columns count items per stage and flag the WIP breach", () => {
    const doc = new DOMParser().parseFromString(
      RENDERERS.pipeline(ctx),
      "text/html",
    );
    expect(
      [...doc.querySelectorAll(".col .n")]
        .map((n) => +n.textContent)
        .reduce((a, b) => a + b, 0),
    ).toBe(ctx.D.items.length);
    expect(doc.querySelector('.col[data-stage="build"]').className).toContain(
      "over",
    );
  });
  test("explorer heat-map: cell sums equal the item count", () => {
    const html = RENDERERS.explorer(
      ctx,
      { dimA: "team", dimB: "stage", measure: "count" },
      { id: "x" },
    );
    const cells = [
      ...new DOMParser()
        .parseFromString(html, "text/html")
        .querySelectorAll("td[data-r]"),
    ].map((c) => +c.textContent || 0);
    expect(cells.reduce((a, b) => a + b, 0)).toBe(ctx.D.items.length);
  });
});

describe("pure helpers", () => {
  test("applyFilter composes dimension drills", () => {
    const f = applyFilter(ctx.D.items, {
      dims: { team: "platform", stage: "build" },
    });
    expect(f.length).toBeGreaterThan(0);
    expect(f.every((i) => i.team === "platform" && i.stage === "build")).toBe(
      true,
    );
  });
  test("measures", () => {
    expect(measureOf(ctx.D, ctx.D.items, "count")).toBe(ctx.D.items.length);
    expect(measureOf(ctx.D, ctx.D.items, "viol")).toBe(
      violationsOf(ctx.D, ctx.D.items).length,
    );
  });
});

describe("safety", () => {
  test("markdown escapes HTML", () => {
    expect(md("<img src=x onerror=alert(1)> **b**")).not.toContain("<img");
    expect(md("- a\n- b")).toBe("<ul><li>a</li><li>b</li></ul>");
  });
  test("image slides refuse javascript: URLs", () => {
    expect(renderImage(ctx, { url: "javascript:alert(1)" })).toContain(
      "No image selected",
    );
    expect(renderImage(ctx, { url: "data:image/png;base64,AAAA" })).toContain(
      "<img",
    );
  });
  test("titles and violation details are escaped", () => {
    const evil = {
      ...ctx,
      D: {
        ...ctx.D,
        violations: [
          {
            policy: "<b>x</b>",
            category: "c",
            severity: "info",
            target: ctx.D.items[0].key,
            person: null,
            team: null,
            project: ctx.D.items[0].project,
            detail: "<script>1</script>",
          },
        ],
      },
      restricted: () => false,
    };
    expect(RENDERERS.violations(evil, {}, { id: "v" })).not.toMatch(
      /<script>|<b>x/,
    );
  });
});

describe("restricted slides", () => {
  test("a restricted habits slide says so instead of drawing data", () => {
    const c = makeContext({ ...data, slide_data: { h: { restricted: true } } });
    expect(text(RENDERERS.habits(c, {}, { id: "h" }))).toContain("Restricted");
  });
});

describe("pagination", () => {
  test("the nine golden slides stay nine; a long table continues on the next slide", () => {
    expect(expandSlides(data.presentation.slides, ctx)).toHaveLength(9);
    const many = {
      ...data,
      slide_data: {
        t: {
          items: Array.from({ length: 30 }, (_, i) => ({
            ...data.snapshot.items[0],
            key: `X-${i}`,
          })),
        },
      },
    };
    const c = makeContext(many);
    const out = expandSlides(
      [{ id: "t", kind: "table", config: {}, title: "T" }],
      c,
    );
    expect(out).toHaveLength(3);
    expect(out[1]._cont).toBe(true);
  });
});
