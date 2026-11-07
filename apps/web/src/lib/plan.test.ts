import { describe, expect, it } from "vitest";
import { axisOf, barOf, dateEditError, deliverablesOf } from "./plan";

describe("roadmap geometry", () => {
  const rows = [
    { planned_start: "2026-09-01", planned_end: "2026-10-01" },
    { planned_start: "2026-10-01", planned_end: "2026-12-01" },
  ];
  it("pads the axis and keeps every bar inside 0..100 %", () => {
    const axis = axisOf(rows)!;
    expect(axis.from).toBeLessThan(Date.parse("2026-09-01"));
    for (const r of rows) {
      const b = barOf(r, axis)!;
      expect(b.left).toBeGreaterThanOrEqual(0);
      expect(b.left + b.width).toBeLessThanOrEqual(100.0001);
    }
    expect(barOf(rows[0], axis)!.width).toBeLessThan(barOf(rows[1], axis)!.width);
  });
  it("a deliverable with only an end date is a point-sized bar; no dates, no bar", () => {
    const axis = axisOf(rows)!;
    expect(barOf({ planned_end: "2026-10-15" }, axis)!.width).toBeGreaterThan(0);
    expect(barOf({}, axis)).toBeNull();
    expect(axisOf([{}])).toBeNull();
  });
  it("rejects an end before the start and non-dates", () => {
    expect(dateEditError("2026-10-02", "2026-10-01")).toMatch(/before/);
    expect(dateEditError("nope", null)).toBe("not a date");
    expect(dateEditError("2026-10-01", "2026-10-01")).toBeNull();
    expect(dateEditError(null, "2026-10-01")).toBeNull();
  });
  it("flattens deliverables across phases", () => {
    expect(
      deliverablesOf({
        phases: [{ deliverables: [{ key: "a" }] }, { deliverables: [{ key: "b" }, { key: "c" }] }],
      }),
    ).toHaveLength(3);
  });
});
