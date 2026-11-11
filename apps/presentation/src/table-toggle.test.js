import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { makeContext } from "./context.js";
import { RENDERERS } from "./renderers/index.js";

const data = JSON.parse(readFileSync("fixtures/golden.json", "utf8"));
const ctx = makeContext(data);
const asTable = { ...ctx, tableMode: () => true };
const doc = (html) => new DOMParser().parseFromString(html, "text/html");

describe("table toggle on the radar and pipeline slides (review 13 M7)", () => {
  test("the pipeline offers a toggle and its table lists every stage with the same counts", () => {
    const slide = { id: "pipe1" };
    const chart = doc(RENDERERS.pipeline(ctx, {}, slide));
    const btn = chart.querySelector("button.tbl-toggle");
    expect(btn.dataset.toggle).toBe("pipe1");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    expect(chart.querySelectorAll(".col").length).toBe(ctx.D.stages.length);

    const t = doc(RENDERERS.pipeline(asTable, {}, slide));
    expect(
      t.querySelector("button.tbl-toggle").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(t.querySelectorAll(".col").length).toBe(0);
    const rows = [...t.querySelectorAll("tbody tr")];
    expect(rows.map((r) => r.querySelector("th").textContent)).toEqual(
      ctx.D.stages,
    );
    const counts = rows.map((r) => +r.querySelector("td").textContent);
    expect(counts.reduce((a, b) => a + b, 0)).toBe(ctx.D.items.length);
    expect(t.querySelector("tr.over")).toBeTruthy(); // the WIP breach on `build` stays visible
  });

  test("the pipeline still renders without a slide (callers that pass only the context)", () => {
    expect(RENDERERS.pipeline(ctx)).toContain('class="pipe"');
  });

  test("the radar offers a toggle and its table is flat, keeps keys and opens the drawer", () => {
    const slide = { id: "radar1" };
    const cfg = { days: 365 };
    const chart = doc(RENDERERS.radar(ctx, cfg, slide));
    expect(chart.querySelector("button.tbl-toggle").dataset.toggle).toBe(
      "radar1",
    );
    const t = doc(RENDERERS.radar(asTable, cfg, slide));
    expect(
      t.querySelector("button.tbl-toggle").getAttribute("aria-pressed"),
    ).toBe("true");
    const flat = [...t.querySelectorAll("tbody tr[data-open]")].map(
      (r) => r.dataset.open,
    );
    const grouped = [...chart.querySelectorAll("tbody tr[data-open]")].map(
      (r) => r.dataset.open,
    );
    expect(flat.length).toBeGreaterThan(0);
    // the table holds what the grouped list shows plus the overdue items (flagged), so nothing is hidden by the toggle
    for (const k of grouped) expect(flat).toContain(k);
    const extra = flat.filter((k) => !grouped.includes(k));
    for (const k of extra)
      expect(t.querySelector(`tr[data-open="${k}"]`).textContent, k).toContain(
        "overdue",
      );
    expect(t.querySelectorAll("td[colspan]").length).toBe(0); // no day-group rows
    expect(t.querySelector("th").textContent).toBe("due");
  });
});
