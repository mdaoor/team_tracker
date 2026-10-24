import { describe, expect, test } from "vitest";
import { slideFromView } from "./slides";
import type { ViewState } from "./viewState";

const base: ViewState = {
  chips: [{ id: "a", label: "project WEB", clause: { project: { in: ["WEB"] } } }],
  layout: "list",
  groupBy: null,
  sort: "weight:desc",
  scheme: null,
  matrix: { rows: "team", cols: "stage", measure: "count" },
  drill: [],
};

describe("Add to presentation", () => {
  test("a matrix view becomes a live chart slide with the frozen filter", () => {
    expect(slideFromView({ ...base, layout: "matrix" })).toEqual({
      kind: "chart",
      title: "team × stage",
      config: {
        rows: "team",
        cols: "stage",
        measure: "count",
        filter: { project: { in: ["WEB"] } },
      },
      live: true,
    });
  });
  test("any other layout becomes a table slide carrying filter and sort", () => {
    const s = slideFromView({ ...base, layout: "board" });
    expect(s.kind).toBe("table");
    expect(s.config).toMatchObject({
      filter: { project: { in: ["WEB"] } },
      sort: [{ field: "weight", dir: "desc" }],
    });
    expect(s.live).toBe(true);
  });
  test("negated chips survive as `not`", () => {
    const s = slideFromView({ ...base, chips: [{ ...base.chips[0], negate: true }] });
    expect(s.config.filter).toEqual({ and: [{ not: { project: { in: ["WEB"] } } }] });
  });
});
