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

import {
  autoBody,
  autoFrom,
  changedLabel,
  chipsFromFilter,
  emptyAuto,
  emptyNotify,
  notifyBody,
  notifyFrom,
  restoreBody,
  sparkPoints,
} from "./governance";

describe("policy editors", () => {
  it("notify round-trips and an empty editor is {}", () => {
    expect(notifyBody(emptyNotify())).toEqual({});
    const n = {
      person: true,
      reporting_line: ["line", "senior"],
      roles: ["manager"],
      channel: "slack",
    };
    expect(notifyBody(notifyFrom(notifyBody(n)))).toEqual(notifyBody(n));
    expect(notifyFrom(null)).toEqual(emptyNotify());
  });
  it("auto action round-trips every kind and is undefined when empty", () => {
    expect(autoBody(emptyAuto())).toBeUndefined();
    const a = { priority: "high", stage: "test", createTitle: "Follow up", createProject: "WEB" };
    expect(autoFrom(autoBody(a))).toEqual(a);
    expect(autoBody({ ...emptyAuto(), createTitle: "  " })).toBeUndefined();
  });
  it("candidate filter becomes one removable chip per clause", () => {
    let n = 0;
    const chips = chipsFromFilter(
      { project: { in: ["WEB"] }, and: [{ text: "x" }, { tags: { any: ["a"] } }] },
      () => `c${n++}`,
    );
    expect(chips).toHaveLength(3);
    expect(chips[0].clause).toEqual({ project: { in: ["WEB"] } });
    expect(chipsFromFilter(null, () => "x")).toEqual([]);
  });
  it("restore writes back the definition: params for template rules, raw CEL otherwise", () => {
    expect(
      restoreBody({
        name: "n",
        severity: "info",
        condition_template_id: "t",
        template_params: { a: 1 },
        condition_cel: "x",
      }),
    ).toEqual({
      name: "n",
      severity: "info",
      params: { a: 1 },
    });
    expect(restoreBody({ name: "n", condition_cel: "item.age > 1" })).toEqual({
      name: "n",
      condition_cel: "item.age > 1",
    });
    expect(changedLabel([])).toBe("no field changes");
    expect(changedLabel(["grace_period", "scope_id"])).toBe("grace period, scope id");
  });
  it("sparkline leaves a gap for a null week and ends on the last real point", () => {
    const { segments, last } = sparkPoints([0.1, 0.2, null, 0.4, 0.3]);
    expect(segments).toHaveLength(2);
    expect(last).not.toBeNull();
    expect(sparkPoints([null, null])).toEqual({ segments: [], last: null });
    expect(sparkPoints([0.5]).segments).toHaveLength(1);
  });
});
