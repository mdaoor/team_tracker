import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { makeContext } from "./context.js";
import { md, renderImage } from "./renderers/misc.js";
import { KIND_META, RENDERERS } from "./renderers/index.js";
import { expandSlides, slideBody } from "./template.js";
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

const planD = (over = {}) => ({
  as_of: "2026-10-24T10:00:00+00:00",
  items: [],
  risks: [],
  teams: [],
  projects: [
    {
      key: "ERP",
      name: "ERP rollout",
      owner: "@mahmoud",
      planned_start: "2026-09-01",
      planned_end: "2027-04-30",
      status: "not_started",
      health: "red",
      baseline_name: "approved",
      summary: "",
      product: null,
      methodology: "",
      team: null,
    },
  ],
  milestones: [
    {
      name: "Kick-off",
      project: "ERP",
      due: "2026-09-30",
      kind: "gate",
      status: "overdue",
      deliverables_done: 0,
      deliverables_total: 1,
    },
    {
      name: "Go-live",
      project: "ERP",
      due: "2027-04-30",
      kind: "release",
      status: "planned",
      deliverables_done: 0,
      deliverables_total: 0,
    },
  ],
  phases: [
    {
      key: "ERP-1",
      title: "Foundation",
      project: "ERP",
      planned_start: "2026-09-01",
      planned_end: "2026-11-15",
      status: "not_started",
      progress_pct: null,
      deliverable_count: 2,
    },
  ],
  deliverables: [
    {
      key: "ERP-2",
      title: "Governance",
      project: "ERP",
      phase: "ERP-1",
      owner: "@sara",
      planned_start: "2026-09-01",
      planned_end: "2026-09-30",
      baseline_end: "2026-09-25",
      baseline_variance_days: 5,
      status: "overdue",
      progress_pct: null,
      linked_tasks: 0,
      hours_logged: 0,
      depends_on: [],
    },
    {
      key: "ERP-3",
      title: "Infrastructure",
      project: "ERP",
      phase: "ERP-1",
      owner: null,
      planned_start: "2026-10-01",
      planned_end: "2026-10-31",
      baseline_end: null,
      baseline_variance_days: null,
      status: "at_risk",
      progress_pct: 40,
      linked_tasks: 5,
      hours_logged: 12.5,
      depends_on: ["ERP-2"],
    },
  ],
  ...over,
});
const pctx = (D) => ({
  D,
  items: D.items,
  slideData: {},
  tableMode: () => false,
});

describe("plan slide kinds render from the plan alone", () => {
  test("draws every plan kind with zero reported work", () => {
    const ctx = pctx(planD());
    for (const kind of [
      "roadmap",
      "milestones",
      "deliverables",
      "plan_vs_actual",
      "phase_status",
      "project_overview",
    ]) {
      const html = RENDERERS[kind](ctx, {}, {});
      expect(html.length, kind).toBeGreaterThan(80);
      expect(html, kind).not.toContain("NaN");
      expect(html, kind).not.toContain("undefined");
    }
  });
  test("shows statuses, variance, dependencies and the baseline ghost", () => {
    const ctx = pctx(planD());
    expect(RENDERERS.deliverables(ctx, {}, {})).toContain("Overdue");
    expect(RENDERERS.plan_vs_actual(ctx, {}, {})).toContain("+5 d");
    const road = RENDERERS.roadmap(ctx, {}, {});
    expect(road).toContain("rm-dep");
    expect(road).toContain("rm-ghost");
    expect(road).toContain("rm-today");
    expect(RENDERERS.milestones(ctx, {}, {})).toContain("0 / 1");
  });
  test("says so instead of drawing an empty chart when there is no plan", () => {
    const ctx = pctx(
      planD({ phases: [], deliverables: [], milestones: [], projects: [] }),
    );
    for (const kind of [
      "roadmap",
      "milestones",
      "deliverables",
      "plan_vs_actual",
      "phase_status",
      "project_overview",
    ])
      expect(RENDERERS[kind](ctx, {}, {}), kind).toContain("No ");
  });
  test("activity kinds say no work was reported, and every kind declares requires/render_empty", () => {
    const ctx = pctx(planD());
    expect(slideBody(ctx, { kind: "kpis", config: {} })).toContain(
      "No work reported yet",
    );
    expect(slideBody(ctx, { kind: "pipeline", config: {} })).toContain(
      "No work reported yet",
    );
    for (const k of Object.keys(RENDERERS)) {
      expect(KIND_META[k], k).toBeTruthy();
      expect(["plan", "activity", "both"]).toContain(KIND_META[k].requires);
      expect(["hide", "placeholder", "note"]).toContain(
        KIND_META[k].render_empty,
      );
    }
  });
  test("paginates long deliverable tables", () => {
    const many = Array.from({ length: 25 }, (_, i) => ({
      ...planD().deliverables[0],
      key: `ERP-${i + 10}`,
      title: `D${i}`,
    }));
    const ctx = pctx(planD({ deliverables: many }));
    const out = expandSlides(
      [{ id: "s", kind: "deliverables", config: {} }],
      ctx,
    );
    expect(out.length).toBe(3);
  });
});

describe("Phase 13 R6.5: long slides fit", () => {
  const many = (n) => ({
    ...structuredClone(data),
    snapshot: {
      ...structuredClone(data.snapshot),
      projects: Array.from({ length: n }, (_, i) => ({ key: `P${i}`, name: `Project ${i}`, status: "active" })),
      items: Array.from({ length: n }, (_, i) => ({ ...data.snapshot.items[0], key: `P${i}-1`, project: `P${i}` })),
    },
  });
  test("paginate (default) makes continuation slides; shrink keeps one slide and scales it", () => {
    const c = makeContext(many(9));
    const base = { id: "s", kind: "projects", config: {} };
    const pages = expandSlides([base], c);
    expect(pages.length).toBeGreaterThan(1);
    expect(pages[1]._cont).toBe(true);
    const one = expandSlides([{ ...base, fit: "shrink" }], c);
    expect(one).toHaveLength(1);
    expect(one[0]._shrink).toBe(0.5); // 4 of 9 would be 0.44: never smaller than half size
    const five = expandSlides([{ ...base, fit: "shrink" }], makeContext(many(5)));
    expect(five[0]._shrink).toBeCloseTo(0.8, 2);
  });
  test("a plan-only project (deliverables, no reported work) still gets a card", () => {
    const d = structuredClone(data);
    d.snapshot.projects.push({ key: "ERP", name: "ERP Rollout", status: "active" });
    d.snapshot.deliverables = [{ key: "ERP-1", project: "ERP", title: "Design", phase: "Design" }];
    const c = makeContext(d);
    expect(c.projectCount()).toBe(data.snapshot.projects.filter((p) => data.snapshot.items.some((i) => i.project === p.key)).length + 1);
    expect(text(slideBody(c, { kind: "projects", config: {} }))).toContain("ERP Rollout");
  });
});
