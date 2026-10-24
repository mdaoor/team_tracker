import { describe, expect, it } from "vitest";
import { completionsFor } from "../pages/governance/CelEditor";
import {
  canActivate,
  controlFor,
  evidenceChips,
  formulaFrom,
  groupViolations,
  habitsCsv,
  paramsFrom,
  problemRange,
  seriesOf,
  trendGlyph,
} from "./governance";

describe("template parameters → form", () => {
  it("builds a number input with bounds from an int param", () => {
    const c = controlFor({ name: "days", type: "int", min: 1, max: 365, default: null, help: "h" });
    expect(c).toMatchObject({ kind: "number", min: 1, max: 365, step: 1, placeholder: "default" });
  });
  it("uses a select for a stage ident when stages are known", () => {
    expect(controlFor({ name: "stage", type: "ident", help: "" }, ["build", "test"])).toMatchObject(
      {
        kind: "select",
        options: ["build", "test"],
      },
    );
    expect(controlFor({ name: "stage", type: "ident", help: "" }).kind).toBe("text");
  });
  it("drops empty fields and turns numbers into numbers", () => {
    const cs = [
      controlFor({ name: "days", type: "int", help: "" }),
      controlFor({ name: "who", type: "string", help: "" }),
    ];
    expect(paramsFrom(cs, { days: "3", who: " " })).toEqual({ days: 3 });
  });
});

describe("CEL errors → editor ranges", () => {
  it("points at the line and column", () => {
    expect(problemRange("a\nbcd ef", { message: "x", line: 2, column: 3 })).toEqual([4, 5]);
  });
  it("marks the whole line when there is no column and clamps out-of-range positions", () => {
    expect(problemRange("abc", { message: "x" })).toEqual([0, 3]);
    expect(problemRange("abc", { message: "x", line: 9, column: 99 })[0]).toBeLessThanOrEqual(3);
  });
});

describe("completion from the context schema", () => {
  const schema = {
    roots: { item: "WorkItem", now: "timestamp" },
    objects: { WorkItem: { weight: "double", priority: "string" } },
    functions: [
      { name: "days_in_stage", receiver: "WorkItem", args: [], returns: "int", doc: "d" },
    ],
  };
  it("offers fields and receiver functions after a dot, roots otherwise", () => {
    const after = completionsFor(schema, "item.").map((c) => c.label);
    expect(after).toEqual(expect.arrayContaining(["weight", "priority", "days_in_stage"]));
    expect(completionsFor(schema, "it").map((c) => c.label)).toContain("item");
  });
});

describe("violations inbox", () => {
  const rows = [
    { id: "1", severity: "warning", policy: "p2" },
    { id: "2", severity: "critical", policy: "p1" },
    { id: "3", severity: "warning", policy: "p2" },
  ];
  it("groups severity first (critical on top), then policy", () => {
    const g = groupViolations(rows);
    expect(g.map((x) => x.severity)).toEqual(["critical", "warning"]);
    expect(g[1].policies[0].rows).toHaveLength(2);
  });
  it("renders evidence as field: value chips and drops internals", () => {
    expect(
      evidenceChips({ item: "WEB-1", days_in_stage: 5, policy: "x", nested: { a: 1 }, none: null }),
    ).toEqual(["item: WEB-1", "days in stage: 5"]);
  });
});

describe("habits", () => {
  it("maps trend to glyphs", () => {
    expect([trendGlyph("improving"), trendGlyph("stable"), trendGlyph("worsening")]).toEqual([
      "↑",
      "→",
      "↓",
    ]);
  });
  it("stamps the 1:1 export and escapes quotes", () => {
    const csv = habitsCsv(
      [{ subject: "@a", key: 'k"q', polarity: "negative", window: "30d" }],
      "@boss",
      new Date(0),
    );
    expect(csv.split("\n")[0]).toBe("# Exported by @boss at 1970-01-01T00:00:00.000Z");
    expect(csv).toContain('"k""q"');
  });
});

describe("activation gate", () => {
  const p = { updated_at: "2026-01-02T00:00:00Z" };
  it("requires a dry run after the last edit; admins may force", () => {
    expect(canActivate({ ...p, last_test: { ran_at: "2026-01-03T00:00:00Z" } }, false)).toBe("yes");
    expect(canActivate({ ...p, last_test: { ran_at: "2026-01-01T00:00:00Z" } }, false)).toBe("no");
    expect(canActivate({ ...p, last_test: null }, true)).toBe("force");
  });
});

describe("weights and metrics", () => {
  it("builds the formula the server validates", () => {
    expect(
      formulaFrom(
        "constant",
        "2",
        [
          { key: "priority.high", factor: "3" },
          { key: "", factor: "1" },
        ],
        "0",
        "9",
      ),
    ).toEqual({
      base: "constant",
      constant: 2,
      multipliers: { "priority.high": 3 },
      clamp: [0, 9],
    });
  });
  it("collapses nested violation counts to a total", () => {
    expect(
      seriesOf(
        [{ date: "d", metrics: { violation_count: { warning: 2, critical: 1 } } }],
        "violations",
      )[0].v,
    ).toBe(3);
  });
});
