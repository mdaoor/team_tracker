import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { makeContext } from "./context.js";
import { snapshotRows } from "./gantt.js";
import { RENDERERS } from "./renderers/index.js";

const golden = makeContext(
  JSON.parse(readFileSync("fixtures/golden.json", "utf8")),
);
const doc = (html) => new DOMParser().parseFromString(html, "text/html");

describe("the deck's `gantt` slide draws through the shared gantt.js (R6.4)", () => {
  test("epic rows are the default and the SVG is the shared one (rm-* classes, today line, milestones)", () => {
    const html = RENDERERS.gantt(golden, {}, { id: "g1" });
    const d = doc(html);
    expect(d.querySelector("svg.rm-svg")).toBeTruthy();
    expect(d.querySelector("figure.roadmap").dataset.rows).toBe("epic");
    const expected = snapshotRows(golden.D, golden.items, {
      rows: "epic",
    }).rows;
    expect(expected.length).toBeGreaterThan(0);
    expect(d.querySelectorAll("rect.rm-bar.epic")).toHaveLength(
      expected.length,
    );
    expect(d.querySelector(".rm-today")).toBeTruthy();
    expect(d.querySelectorAll(".rm-ms").length).toBeGreaterThan(0);
    expect(html).not.toContain("<polygon"); // the old hand-drawn milestone marker is gone
    expect(html).not.toMatch(/NaN|undefined/);
  });

  test("it keeps the table toggle; the table lists the same epics and milestones", () => {
    const slide = { id: "g1" };
    const chart = doc(RENDERERS.gantt(golden, {}, slide));
    expect(chart.querySelector("button.tbl-toggle").dataset.toggle).toBe("g1");
    const t = doc(
      RENDERERS.gantt({ ...golden, tableMode: () => true }, {}, slide),
    );
    expect(t.querySelector("svg")).toBeNull();
    expect(
      t.querySelector("button.tbl-toggle").getAttribute("aria-pressed"),
    ).toBe("true");
    const names = [...t.querySelectorAll("tbody tr td:first-child")].map(
      (c) => c.textContent,
    );
    for (const r of snapshotRows(golden.D, golden.items, { rows: "epic" }).rows)
      expect(names.some((n) => n.includes(r.key))).toBe(true);
    expect(names.some((n) => n.includes("◆"))).toBe(true);
  });

  test("rows=item draws one bar per scheduled item, rows=milestone one per milestone; an unknown value falls back to epics", () => {
    const withDates = golden.items.filter(
      (i) => i.planned_start && i.planned_end,
    );
    const item = doc(RENDERERS.gantt(golden, { rows: "item" }, { id: "g" }));
    expect(item.querySelectorAll("rect.rm-bar.item")).toHaveLength(
      withDates.length,
    );
    const ms = doc(RENDERERS.gantt(golden, { rows: "milestone" }, { id: "g" }));
    expect(ms.querySelectorAll("rect.rm-bar.milestone").length).toBeGreaterThan(
      0,
    );
    const bad = doc(RENDERERS.gantt(golden, { rows: "nope" }, { id: "g" }));
    expect(bad.querySelector("figure.roadmap").dataset.rows).toBe("epic");
  });

  test("baseline=true ghosts the planned end when an item carries a baseline", () => {
    const raw = JSON.parse(readFileSync("fixtures/golden.json", "utf8"));
    const first = raw.snapshot.items.find(
      (i) => i.planned_start && i.planned_end,
    );
    first.baseline_end = first.planned_start;
    const ctx = makeContext(raw);
    const html = RENDERERS.gantt(
      ctx,
      { rows: "item", baseline: true },
      { id: "g" },
    );
    expect(html).toContain("rm-ghost");
    expect(RENDERERS.gantt(ctx, { rows: "item" }, { id: "g" })).not.toContain(
      "rm-ghost",
    );
  });

  test("says so when nothing is scheduled", () => {
    const ctx = { ...golden, items: [] };
    expect(RENDERERS.gantt(ctx, {}, { id: "g" })).toContain("No scheduled");
  });
});
