import { describe, expect, it } from "vitest";
import contract from "./contract.json";
import { demoBridge } from "./demo";
import { makeBridge, personalCall } from "./sidecar";
import { BridgeError, CATEGORIES } from "./types";

/** `contract.json` is written by the Python suite (packages/client_core/tests/test_personal_bridge.py) from the sidecar's real output.
 * These tests keep the TypeScript side honest: the in-memory double the views are tested with must have the same shapes, and every
 * bridge method must call a command that exists. */
type Shape = string | Shape[] | { [k: string]: Shape };
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export function conforms(value: unknown, shape: Shape, path = "$"): string[] {
  if (shape === "null") return []; // Python saw None here: the type is not known
  if (value === null || value === undefined) return []; // nullable
  if (typeof shape === "string")
    return typeof value === shape
      ? []
      : [`${path}: ${typeof value} is not ${shape}`];
  if (Array.isArray(shape)) {
    if (!Array.isArray(value)) return [`${path}: not an array`];
    if (shape.length === 0) return [];
    return value.flatMap((v, i) => conforms(v, shape[0], `${path}[${i}]`));
  }
  if (typeof value !== "object" || Array.isArray(value))
    return [`${path}: not an object`];
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(shape);
  const isMap = keys.length > 0 && keys.every((k) => DATE.test(k));
  const isPartialMinutes =
    keys.length > 0 &&
    keys.every((k) => (CATEGORIES as string[]).includes(k)) &&
    path.endsWith("minutes");
  if (isMap) {
    const [first] = Object.values(shape);
    return Object.entries(obj).flatMap(([k, v]) =>
      DATE.test(k)
        ? conforms(v, first, `${path}.${k}`)
        : [`${path}: key ${k} is not a date`],
    );
  }
  const errs: string[] = [];
  if (!isPartialMinutes)
    for (const k of keys) if (!(k in obj)) errs.push(`${path}.${k}: missing`);
  for (const k of Object.keys(obj)) {
    if (!(k in shape)) {
      if (!isPartialMinutes) errs.push(`${path}.${k}: not in the contract`);
      continue;
    }
    errs.push(...conforms(obj[k], shape[k], `${path}.${k}`));
  }
  return errs;
}

const shapes = contract.shapes as unknown as Record<string, Shape>;

describe("the bridge contract written by the Python sidecar tests", () => {
  it("lists the commands the bridge calls", async () => {
    const called: string[] = [];
    const b = makeBridge(async (_cmd, a) => {
      called.push((a as { command: string }).command);
      return {};
    });
    await Promise.all([
      b.day(),
      b.week(),
      b.month(2026, 10),
      b.preview("x"),
      b.add("x"),
      b.recurAdd("x"),
      b.complete("i"),
      b.skip("i"),
      b.reopen("i"),
      b.remove("i"),
      b.reschedule("i", "2026-10-23T09:00:00+03:00"),
      b.setCategory("i", "work"),
      b.timerStart({ id: "i" }),
      b.timerStop(),
      b.timerStatus(),
      b.tray(),
      b.send({ id: "i" }),
      b.notWork("i"),
      b.unsend("i"),
      b.rememberShare("work", "ask"),
      b.eod(),
      b.eodSendAll(),
      b.eodConfirm(),
      b.weeklyReview(),
      b.developmentSummary(),
      b.deliver(),
      b.privacy(),
      b.firstRun({
        work: "ask",
        meeting: "ask",
        development: false,
        assist: false,
      }),
      b.sent(),
      b.eraseLocal(),
      b.calSettings(),
      b.calExclude("c", true),
      b.calSuppress("domain", "x.com"),
      b.calRefresh(),
      b.calSync(),
      b.notifications(),
    ]);
    expect(called.length).toBe(36);
    for (const c of called) expect(contract.commands, c).toContain(c);
  });

  it("turns sidecar errors into BridgeError with the alternatives", async () => {
    const call = async () => ({
      error: {
        code: "needs_project",
        message: "choose a project",
        alternatives: ["WEB", "DATA", "OPS"],
      },
    });
    await expect(personalCall("personal_send", {}, call)).rejects.toMatchObject(
      { code: "needs_project", alternatives: ["WEB", "DATA", "OPS"] },
    );
    await expect(
      personalCall("personal_send", {}, call),
    ).rejects.toBeInstanceOf(BridgeError);
    expect(
      await personalCall("personal_day", {}, async () => ({ date: "x" })),
    ).toEqual({ date: "x" });
  });

  it("matches the demo bridge's answers (so the view tests run on real shapes)", async () => {
    const b = demoBridge();
    await b.timerStart({ id: "t-login" });
    await b.send({ id: "m-sprint" });
    const samples: Record<string, unknown> = {
      personal_day: await b.day(),
      personal_week: await b.week(),
      personal_month: await b.month(2026, 10),
      personal_tray: await b.tray(),
      personal_eod: await b.eod(),
      personal_weekly_review: await b.weeklyReview(),
      personal_privacy: await b.privacy(),
      personal_sent: await b.sent(),
      personal_preview: await b.preview("every weekday 7:00 run 30m"),
      personal_send: await b.send({ id: "m-client" }),
      personal_timer_status: await b.timerStatus(),
      personal_cal_settings: await b.calSettings(),
    };
    for (const [cmd, value] of Object.entries(samples)) {
      expect(conforms(value, shapes[cmd], cmd), cmd).toEqual([]);
    }
  });

  it("the checker itself catches drift", () => {
    expect(conforms({ a: 1 }, { a: "string" })).toEqual([
      "$.a: number is not string",
    ]);
    expect(conforms({}, { a: "string" })).toEqual(["$.a: missing"]);
    expect(conforms({ a: "x", b: 2 }, { a: "string" })).toEqual([
      "$.b: not in the contract",
    ]);
    expect(conforms({ a: null }, { a: "string" })).toEqual([]);
  });
});
