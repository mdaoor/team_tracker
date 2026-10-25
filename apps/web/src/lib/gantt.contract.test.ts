import { expect, test } from "vitest";
import { layoutGantt as shared } from "@tt/presentation/gantt";

/** Contract with the shared Gantt (apps/presentation): the API's GanttRow shape and the web's label/axis sizes. */
const LABEL_W = 170;
const AXIS_H = 34;
const ROW_H = 26;
type Z = "day" | "week" | "month";
type Rows = Parameters<typeof shared>[0]["rows"];
const layoutGantt = (
  rs: Rows,
  deps: { from: string; to: string; type?: string }[],
  milestones: { name: string; project: string; due: string }[],
  today: string | null,
  zoom: Z,
) =>
  shared({
    rows: rs.filter((r) => r.planned_start && r.planned_end),
    deps: deps.map(({ from, to }) => ({ from, to })),
    milestones,
    today,
    zoom,
    labelWidth: LABEL_W,
    axisHeight: AXIS_H,
    rowHeight: ROW_H,
  });

const rows = [
  {
    key: "WEB-1",
    title: "Checkout",
    type: "epic",
    planned_start: "2026-10-05",
    planned_end: "2026-10-16",
    state_category: "active",
    rollup: true,
    baseline: { planned_start: "2026-10-05", planned_end: "2026-10-12" },
  },
  {
    key: "WEB-2",
    title: "Search",
    type: "epic",
    planned_start: "2026-10-12",
    planned_end: "2026-10-30",
    state_category: "done",
  },
  { key: "WEB-3", title: "Unscheduled", type: "epic", planned_start: null, planned_end: null },
];

test("bars sit right of the label column, in order, one row each, unscheduled rows are left out", () => {
  const m = layoutGantt(rows, [], [], "2026-10-14", "week");
  expect(m.bars.map((b) => b.key)).toEqual(["WEB-1", "WEB-2"]);
  expect(m.bars.every((b) => b.x >= LABEL_W)).toBe(true);
  expect(m.bars[1].y - m.bars[0].y).toBe(ROW_H);
  expect(m.bars[0].y).toBe(AXIS_H);
  expect(m.bars[1].x).toBeGreaterThan(m.bars[0].x);
});

test("a time axis, a today line inside the chart, slipped rollups flagged and the baseline ghost drawn", () => {
  const m = layoutGantt(rows, [], [], "2026-10-14", "week");
  expect(m.ticks.length).toBeGreaterThan(3);
  expect(m.todayX).not.toBeNull();
  expect(m.todayX!).toBeGreaterThan(m.bars[0].x);
  expect(m.todayX!).toBeLessThan(m.bars[0].x + m.bars[0].w);
  expect(m.bars[0].slip).toBe(true); // ends 16 Oct against a baseline of 12 Oct
  expect(m.bars[0].ghost).toBeDefined();
  expect(m.bars[1].done).toBe(true);
});

test("zoom changes the scale: day is wider than week is wider than month", () => {
  const w = (z: Z) => layoutGantt(rows, [], [], null, z).width;
  expect(w("day")).toBeGreaterThan(w("week"));
  expect(w("week")).toBeGreaterThan(w("month"));
});

test("dependencies become elbow arrows between the two bars; milestones become diamonds", () => {
  const m = layoutGantt(
    rows,
    [
      { from: "WEB-1", to: "WEB-2", type: "finish_to_start" },
      { from: "WEB-1", to: "GONE-1" },
    ],
    [{ name: "Beta", project: "WEB", due: "2026-10-20" }],
    null,
    "week",
  );
  expect(m.arrows).toHaveLength(1);
  expect(m.arrows[0].d).toMatch(/^M[\d.]+,[\d.]+ H[\d.]+ V[\d.]+ H[\d.]+$/);
  expect(m.milestones).toHaveLength(1);
  expect(m.milestones[0].name).toBe("Beta");
});

test("an empty chart is safe", () => {
  const m = layoutGantt([], [], [], null, "week");
  expect(m.bars).toEqual([]);
  expect(m.width).toBe(LABEL_W);
});
