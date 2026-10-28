import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { makeContext } from "./context.js";
import { ganttSvg, ganttTable, layoutGantt, snapshotRows } from "./gantt.js";
import { RENDERERS } from "./renderers/index.js";

const rows = [
  { key: "A", title: "Design", type: "phase", planned_start: "2026-09-01", planned_end: "2026-09-30", progress_pct: 100, status: "done" },
  { key: "B", title: "Build <b>", type: "deliverable", depth: 1, planned_start: "2026-10-01", planned_end: "2026-10-31", progress_pct: 40, status: "at_risk", baseline: { planned_end: "2026-10-20" } },
  { key: "C", title: "Unscheduled", type: "deliverable" },
]; // prettier-ignore
const deps = [{ from: "A", to: "B" }, { from: "B", to: "C" }]; // prettier-ignore
const ms = [{ name: "Beta", due: "2026-10-15", status: "due_soon" }];
const today = "2026-10-10";
const text = (html) => new DOMParser().parseFromString(html, "text/html");

describe("layoutGantt (R6.4: one Gantt for deck and web)", () => {
  test("axis, today line, milestones, dependency arrows and bar geometry", () => {
    const m = layoutGantt({ rows, deps, milestones: ms, today, zoom: "week" });
    expect(m.bars).toHaveLength(3);
    expect(m.bars[0].x).toBeLessThan(m.bars[1].x);
    expect(m.todayX).toBeGreaterThan(m.bars[0].x);
    expect(m.milestones[0].x).toBeGreaterThan(m.todayX);
    expect(m.arrows).toHaveLength(1); // C has no dates: no arrow into it
    expect(m.ticks.length).toBeGreaterThan(5);
    expect(m.bars[1].ghost && m.bars[1].slip).toBeTruthy();
    expect(m.bars[2].dated).toBe(false);
    for (const n of [m.width, m.height, ...m.bars.map((b) => b.x + b.w)])
      expect(Number.isFinite(n)).toBe(true);
  });
  test("zoom changes the pixel width; fit keeps the requested width", () => {
    const w = (zoom) =>
      layoutGantt({ rows, deps, milestones: ms, today, zoom, width: 800 });
    expect(w("day").width).toBeGreaterThan(w("week").width);
    expect(w("week").width).toBeGreaterThan(w("month").width);
    expect(w("fit").width).toBe(800);
    expect(w("day").scroll && !w("fit").scroll).toBe(true);
    expect(w("nonsense").zoom).toBe("fit");
  });
  test("nothing scheduled gives an empty model, not NaN", () => {
    const m = layoutGantt({ rows: [{ key: "x" }] });
    expect(m.bars).toEqual([]);
    expect(ganttSvg(m)).not.toMatch(/NaN|undefined/);
  });
  test("the SVG is escaped, labelled and has a table twin", () => {
    const m = layoutGantt({ rows, deps, milestones: ms, today });
    const svg = ganttSvg(m, { label: "Roadmap" });
    expect(svg).toContain('aria-label="Roadmap"');
    expect(svg).toContain("Build &lt;b&gt;");
    expect(svg).not.toContain("Build <b>");
    for (const c of ["rm-today", "rm-dep", "rm-ghost", "rm-ms", "rm-bar"])
      expect(svg).toContain(c);
    expect(text(ganttTable(m, rows)).querySelectorAll("tbody tr")).toHaveLength(
      3,
    );
  });
});

describe("the deck Roadmap slide uses it", () => {
  const D = {
    as_of: "2026-10-10T08:00:00",
    epics: [{ key: "E1", title: "Epic one", project: "ERP" }],
    milestones: [{ name: "Beta", project: "ERP", due: "2026-10-15", status: "due_soon", deliverables_total: 2, deliverables_done: 1 }],
    phases: [{ key: "P1", project: "ERP", title: "Design", planned_start: "2026-09-01", planned_end: "2026-09-30", status: "done", progress_pct: 100 }],
    deliverables: [{ key: "D1", project: "ERP", phase: "P1", title: "Spec", planned_start: "2026-09-01", planned_end: "2026-09-20", status: "done", depends_on: [] },
                   { key: "D2", project: "ERP", phase: "P1", title: "Review", planned_start: "2026-09-21", planned_end: "2026-09-30", status: "in_progress", depends_on: ["D1"] }],
    risks: [], projects: [{ key: "ERP", name: "ERP" }],
    items: [
      { key: "ERP-1", project: "ERP", epic: "E1", state: "done", planned_start: "2026-09-02", planned_end: "2026-09-12" },
      { key: "ERP-2", project: "ERP", epic: "E1", state: "open", planned_start: "2026-09-10", planned_end: "2026-10-05" },
    ],
  }; // prettier-ignore
  const ctx = (over = {}) => ({
    D,
    items: D.items,
    slideData: {},
    tableMode: () => false,
    ...over,
  });

  test("default rows are phases + deliverables with dependency arrows; fit zoom has no scroll wrapper", () => {
    const html = RENDERERS.roadmap(ctx(), {}, {});
    expect(html).toContain('data-rows="phase"');
    expect(html).toContain('data-zoom="fit"');
    expect(html).not.toContain("rm-scroll");
    expect(html).toContain("rm-dep");
    expect(html).toContain("Roadmap: 3 rows, 1 dependency, 1 milestone shown");
  });
  test("rows=epic rolls items up; rows=milestone draws one bar per milestone", () => {
    const epic = text(RENDERERS.roadmap(ctx(), { rows: "epic" }, {}));
    expect(epic.querySelectorAll("rect.rm-bar")).toHaveLength(1);
    expect(epic.querySelector("rect.rm-bar").classList.contains("epic")).toBe(
      true,
    );
    expect(epic.querySelector(".rm-done").getAttribute("width")).not.toBe(
      "0.0",
    );
    const mil = text(RENDERERS.roadmap(ctx(), { rows: "milestone" }, {}));
    expect(mil.querySelectorAll("rect.rm-bar.milestone")).toHaveLength(1);
    expect(
      snapshotRows(D, D.items, { rows: "milestone" }).rows[0].progress_pct,
    ).toBe(50);
  });
  test("zoom=week scrolls inside a labelled region; page mode offers the selects", () => {
    const html = RENDERERS.roadmap(
      ctx({ interactive: true }),
      { zoom: "week" },
      {},
    );
    expect(html).toContain('class="rm-scroll"');
    expect(html).toContain('data-ctl="zoom"');
    expect(html).toContain('data-ctl="rows"');
    expect(RENDERERS.roadmap(ctx(), { zoom: "week" }, {})).not.toContain(
      "data-ctl",
    );
  });
  test("works on the golden snapshot too (no plan => the empty note, epics => bars)", () => {
    const data = JSON.parse(readFileSync("fixtures/golden.json", "utf8"));
    const c = makeContext(data);
    expect(RENDERERS.roadmap(c, {}, {})).toContain("No roadmap");
    expect(RENDERERS.roadmap(c, { rows: "epic" }, {})).toContain("rm-bar");
  });
});
