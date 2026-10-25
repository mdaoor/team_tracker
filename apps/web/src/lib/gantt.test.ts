import { expect, test } from "vitest";
import { AXIS_H, LABEL_W, ROW_H, layoutGantt } from "./gantt";

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
  const w = (z: "day" | "week" | "month") => layoutGantt(rows, [], [], null, z).width;
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
