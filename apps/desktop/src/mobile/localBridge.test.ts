import { describe, expect, it, vi } from "vitest";
import contract from "../personal/contract.json";
import { conforms } from "../personal/contract.test";
import { BridgeError } from "../personal/types";
import { localBridge, parseLine, STORAGE_KEY, localDate } from "./localBridge";

class MemoryStorage implements Storage {
  m = new Map<string, string>();
  get length() {
    return this.m.size;
  }
  clear() {
    this.m.clear();
  }
  getItem(k: string) {
    return this.m.get(k) ?? null;
  }
  key(i: number) {
    return [...this.m.keys()][i] ?? null;
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  setItem(k: string, v: string) {
    this.m.set(k, v);
  }
}

/** Friday 23 Oct 2026, 15:00 in whatever zone the test machine has. */
function clockAt() {
  const c = { t: new Date(2026, 9, 23, 15, 0, 0) };
  return {
    now: () => new Date(c.t),
    advance: (min: number) => (c.t = new Date(c.t.getTime() + min * 60000)),
  };
}
function setup(
  over: { fetchFn?: typeof fetch; storage?: Storage | null } = {},
) {
  const clock = clockAt();
  const storage =
    over.storage === undefined ? new MemoryStorage() : over.storage;
  const b = localBridge({ storage, now: clock.now, fetchFn: over.fetchFn });
  b.saveSettings({
    handle: "@mahmoud",
    workspace: "acme",
    projects: ["web", "data", "ops"],
  });
  return { b, clock, storage };
}
const shapes = contract.shapes as unknown as Record<
  string,
  Parameters<typeof conforms>[1]
>;

describe("quick add parser", () => {
  const now = new Date(2026, 9, 23, 15, 0, 0);
  it("understands a prefix, a project, a duration and a clock time; the rest is the title", () => {
    const p = parseLine("work: fix login p:web 1h30 at 4pm", now);
    expect(p).toMatchObject({
      title: "fix login",
      category: "work",
      project_hint: "WEB",
      duration_min: 90,
      all_day: false,
      date: "2026-10-23",
    });
    expect(p.start?.slice(11, 16)).toBe("16:00");
    expect(p.end?.slice(11, 16)).toBe("17:30");
  });
  it("a line it cannot place is personal, and a development line is tagged", () => {
    expect(parseLine("run 30m at 18:00", now)).toMatchObject({
      category: "personal",
      kind: "sport",
      duration_min: 30,
    });
    expect(parseLine("course: Rust ch.3 1h", now)).toMatchObject({
      category: "development",
      tags: ["development"],
    });
  });
  it("tomorrow moves the date; a bad clock is explained", () => {
    expect(parseLine("dentist tomorrow 9:30", now).date).toBe("2026-10-24");
    expect(parseLine("dentist at 31:00", now).warnings[0]).toMatch(
      /does not exist/,
    );
  });
  it("repeating lines are flagged, empty lines rejected", () => {
    expect(parseLine("every weekday 7:00 run 30m", now).warnings[0]).toMatch(
      /desktop/,
    );
    expect(() => parseLine("   ", now)).toThrow(/nothing to add/);
  });
});

describe("the local planner", () => {
  it("adds, shows Today, completes, and persists across a reload", async () => {
    const { b, clock, storage } = setup();
    await b.add("work: fix login 2h p:web");
    await b.add("run 30m at 6pm");
    const day = await b.day();
    expect(day.date).toBe("2026-10-23");
    expect(day.timeline.map((r) => r.title)).toEqual(["run"]);
    expect(day.anytime.map((r) => r.title)).toEqual(["fix login"]);
    expect(day.planned_min).toBe(150);
    expect(day.capacity_min).toBe(480);
    const id = day.anytime[0].id;
    await b.complete(id);
    expect((await b.day()).totals.work.done).toBe(120);
    const again = localBridge({ storage: storage as Storage, now: clock.now });
    expect((await again.day()).anytime[0]).toMatchObject({
      title: "fix login",
      status: "done",
    });
  });

  it("the timer is real time, one at a time, and feeds the done minutes", async () => {
    const { b, clock } = setup();
    const a = (await b.add("work: write docs p:web")).items[0];
    await b.timerStart({ id: a.id });
    clock.advance(12);
    expect((await b.timerStatus()).timer).toMatchObject({
      title: "write docs",
      elapsed_min: 12,
    });
    const other = await b.timerStart({ title: "work: review" });
    expect(other.paused).toBe(a.id);
    clock.advance(5);
    expect((await b.timerStop()).stopped).toMatchObject({
      title: "review",
      minutes: 5,
    });
    await b.timerStart({ id: a.id });
    clock.advance(3);
    const done = (await b.complete(a.id)).item;
    expect(done.minutes_done).toBe(15);
    expect((await b.timerStatus()).timer).toBeNull();
  });

  it("week and month are computed from the stored items", async () => {
    const { b } = setup();
    await b.add("work: a 1h");
    await b.add("work: b 30m tomorrow");
    const w = await b.week();
    expect(w.week_start).toBe("2026-10-19");
    expect(w.days).toHaveLength(7);
    expect(w.planned_min).toBe(90);
    const m = await b.month();
    expect([m.year, m.month]).toEqual([2026, 10]);
    expect(Object.keys(m.days)).toEqual(["2026-10-23", "2026-10-24"]);
    expect(m.weeks.every((wk) => wk.length === 7)).toBe(true);
  });

  it("reschedule, skip, reopen, remove", async () => {
    const { b } = setup();
    const it = (await b.add("work: a 1h")).items[0];
    const when = new Date(2026, 9, 26, 9, 0, 0).toISOString();
    const moved = (await b.reschedule(it.id, when)).item;
    expect(moved.start?.slice(0, 10)).toBe("2026-10-26");
    expect(moved.end?.slice(11, 16)).toBe("10:00");
    expect((await b.skip(it.id)).item.status).toBe("skipped");
    expect((await b.reopen(it.id)).item.status).toBe("planned");
    expect((await b.remove(it.id)).deleted).toBe(true);
    await expect(b.skip(it.id)).rejects.toThrow(/no such item/);
  });
});

describe("what the phone cannot do says so", () => {
  it.each([
    [
      "recurAdd",
      (b: ReturnType<typeof setup>["b"]) => b.recurAdd("every weekday run"),
    ],
    [
      "add of a repeating line",
      (b: ReturnType<typeof setup>["b"]) => b.add("every weekday 7:00 run 30m"),
    ],
    ["calendars", (b: ReturnType<typeof setup>["b"]) => b.calSync()],
    [
      "day log confirmation",
      (b: ReturnType<typeof setup>["b"]) => b.eodConfirm(),
    ],
    [
      "development note",
      (b: ReturnType<typeof setup>["b"]) => b.developmentSummary(),
    ],
  ])("%s -> not_on_mobile", async (_n, call) => {
    const { b } = setup();
    await expect(call(b)).rejects.toMatchObject({ code: "not_on_mobile" });
    await expect(call(b)).rejects.toBeInstanceOf(BridgeError);
  });
  it("has no calendars and no auto-send", async () => {
    const { b } = setup();
    expect((await b.calSettings()).calendars).toEqual([]);
    await expect(b.rememberShare("work", "auto")).rejects.toMatchObject({
      code: "not_on_mobile",
    });
    expect((await b.privacy()).encrypted).toBe(false);
  });
});

describe("ready to send and the offline outbox", () => {
  async function doneWork(
    b: ReturnType<typeof setup>["b"],
    line = "work: fix login 2h p:web",
  ) {
    const it = (await b.add(line)).items[0];
    await b.complete(it.id);
    return it.id;
  }

  it("a done work item appears in the tray with the project it was given; a personal one never does", async () => {
    const { b } = setup();
    const id = await doneWork(b);
    const run = (await b.add("run 30m")).items[0];
    await b.complete(run.id);
    const tray = (await b.tray()).entries;
    expect(tray.map((e) => e.id)).toEqual([id]);
    expect(tray[0]).toMatchObject({ minutes: 120, needs_project: false });
    expect(tray[0].suggestion).toMatchObject({ project: "WEB", cue: "✓" });
    await expect(b.send({ id: run.id })).rejects.toMatchObject({
      code: "not_sendable",
    });
  });

  it("an item without a project asks with the configured chips", async () => {
    const { b } = setup();
    const id = await doneWork(b, "work: scratch 20m");
    const [entry] = (await b.tray()).entries;
    expect(entry).toMatchObject({ needs_project: true });
    expect(entry.suggestion.alternatives).toEqual(["WEB", "DATA", "OPS"]);
    await expect(b.send({ id })).rejects.toMatchObject({
      code: "needs_project",
      alternatives: ["WEB", "DATA", "OPS"],
    });
    expect((await b.send({ id, project: "OPS" })).project).toBe("OPS");
  });

  it("a meeting is offered once it ended, and development only after the toggle, on the dev project", async () => {
    const { b, clock } = setup();
    await b.add("meeting: Sprint planning 1h at 1pm p:web");
    expect((await b.tray()).entries.map((e) => e.title)).toEqual([
      "Sprint planning",
    ]);
    await b.add("meeting: Later 1h at 4pm p:web");
    expect((await b.tray()).entries.map((e) => e.title)).toEqual([
      "Sprint planning",
    ]);
    clock.advance(180);
    expect((await b.tray()).entries).toHaveLength(2);
    const dev = (await b.add("course: Rust ch.3 1h")).items[0];
    await b.complete(dev.id);
    expect((await b.tray()).entries.some((e) => e.id === dev.id)).toBe(false);
    await b.rememberShare("development", "ask");
    const r = await b.send({ id: dev.id });
    expect(r).toMatchObject({ project: "DEV", minutes: 60 });
    expect(r.tags).toContain("development");
  });

  it("send queues a log_time batch with ids and the note only, and unsend before delivery takes it back", async () => {
    const { b, storage } = setup();
    const id = await doneWork(b);
    const r = await b.send({ id });
    expect(b.pending()).toBe(1);
    const saved = JSON.parse(
      (storage as Storage).getItem(STORAGE_KEY) ?? "null",
    );
    expect(saved.outbox).toHaveLength(1); // survives a reload: it is in storage, not only in memory
    expect(saved.outbox[0].batch.messages[0].action).toBe("log_time");
    expect((await b.sent()).sent[0]).toMatchObject({
      client_id: r.client_id,
      project: "WEB",
      can_unsend: true,
    });
    expect((await b.unsend(id)).how).toBe("discarded");
    expect(b.pending()).toBe(0);
    expect((await b.tray()).entries.map((e) => e.id)).toEqual([id]);
  });

  it("delivery posts the batch with the token, marks it delivered, and keeps it when offline or when the server is down", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    let mode: "ok" | "down" | "offline" | "reject" = "offline";
    const fetchFn = vi.fn(
      async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init: init ?? {} });
        if (mode === "offline") throw new TypeError("Failed to fetch");
        return new Response("{}", {
          status: mode === "ok" ? 207 : mode === "down" ? 503 : 422,
        });
      },
    ) as unknown as typeof fetch;
    const { b } = setup({ fetchFn });
    b.saveSettings({ server: "https://tt.example.com/", token: "tok-123" });
    const id = await doneWork(b);
    await b.send({ id });
    expect(await b.deliver()).toEqual({
      sent: 0,
      offline: true,
      kept: 1,
      failed: 0,
    });
    mode = "down";
    expect(await b.deliver()).toMatchObject({
      sent: 0,
      offline: true,
      kept: 1,
    });
    mode = "reject";
    expect(await b.deliver()).toMatchObject({
      sent: 0,
      failed: 1,
      offline: false,
    });
    mode = "ok";
    expect(await b.deliver()).toMatchObject({ sent: 1, kept: 0, failed: 0 });
    const last = calls[calls.length - 1];
    expect(last.url).toBe(
      "https://tt.example.com/api/v1/w/acme/capture/batches",
    );
    expect((last.init.headers as Record<string, string>).authorization).toBe(
      "Bearer tok-123",
    );
    const batch = JSON.parse(String(last.init.body));
    expect(batch).toMatchObject({ workspace: "acme", client_version: "0.2" });
    const m = batch.messages[0];
    expect(m).toMatchObject({
      actor: "@mahmoud",
      action: "log_time",
      project: "WEB",
      minutes: 120,
      source: { origin: "personal" },
    });
    expect(Object.keys(m).sort()).toEqual(
      [
        "action",
        "actor",
        "at",
        "client_id",
        "item",
        "minutes",
        "note",
        "project",
        "raw_input",
        "source",
        "tags",
      ].sort(),
    );
    expect((await b.sent()).sent[0].can_unsend).toBe(false); // delivered: taking it back needs the desktop
    await expect(b.unsend(id)).rejects.toMatchObject({ code: "not_on_mobile" });
    expect(await b.deliver()).toEqual({
      sent: 0,
      offline: false,
      kept: 0,
      failed: 0,
    });
  });

  it("sending needs a handle and a workspace first", async () => {
    const b = localBridge({ storage: new MemoryStorage(), now: clockAt().now });
    const it = (await b.add("work: a 1h p:web")).items[0];
    await b.complete(it.id);
    await expect(b.send({ id: it.id })).rejects.toThrow(/handle/);
  });

  it("notifications are returned once per entry", async () => {
    const { b } = setup();
    await doneWork(b);
    expect((await b.notifications()).entries).toHaveLength(1);
    expect((await b.notifications()).entries).toHaveLength(0);
  });

  it("erase removes the data and the token", async () => {
    const { b, storage } = setup();
    b.saveSettings({ token: "secret" });
    await doneWork(b);
    await b.eraseLocal();
    expect((storage as MemoryStorage).getItem(STORAGE_KEY)).toBeNull();
    expect(b.settings().token).toBe("");
    expect((await b.day()).anytime).toEqual([]);
  });
});

describe("the review cards", () => {
  it("end of day and the weekly review are computed from what was done", async () => {
    const { b } = setup();
    const w = (await b.add("work: a 2h p:web")).items[0];
    const d = (await b.add("course: b 1h")).items[0];
    await b.complete(w.id);
    await b.complete(d.id);
    const eod = await b.eod();
    expect(eod).toMatchObject({ done_count: 2, open_count: 0 });
    expect(eod.hours_by_category).toMatchObject({ work: 2, development: 1 });
    expect(eod.unsent.map((e) => e.id)).toEqual([w.id]);
    const res = await b.eodSendAll();
    expect(res.sent).toHaveLength(1);
    const week = await b.weeklyReview();
    expect(week).toMatchObject({
      planned_h: 3,
      done_h: 3,
      completion_pct: 100,
      development_h: 1,
    });
    expect(week.development_items).toEqual([{ title: "b", hours: 1 }]);
  });
});

describe("storage trouble", () => {
  it("works in memory when browser storage is blocked, and ignores a corrupt saved state", async () => {
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
      removeItem: () => undefined,
    } as unknown as Storage;
    const { b } = setup({ storage: broken });
    await b.add("work: a 1h");
    expect((await b.day()).planned_min).toBe(60);
    const corrupt = new MemoryStorage();
    corrupt.setItem(STORAGE_KEY, "{not json");
    expect(
      (await localBridge({ storage: corrupt, now: clockAt().now }).day())
        .timeline,
    ).toEqual([]);
  });
});

describe("shape contract", () => {
  it("answers with the same shapes the Python sidecar writes to contract.json", async () => {
    const { b } = setup();
    const w = (await b.add("work: fix login 2h p:web")).items[0];
    await b.complete(w.id);
    await b.add("meeting: Sprint planning 1h at 1pm p:web");
    await b.timerStart({ title: "reading" });
    const samples: Record<string, unknown> = {
      personal_day: await b.day(),
      personal_week: await b.week(),
      personal_month: await b.month(),
      personal_tray: await b.tray(),
      personal_eod: await b.eod(),
      personal_weekly_review: await b.weeklyReview(),
      personal_privacy: await b.privacy(),
      personal_preview: await b.preview("work: x 30m"),
      personal_timer_status: await b.timerStatus(),
      personal_cal_settings: await b.calSettings(),
    };
    await b.timerStop();
    samples.personal_send = await b.send({ id: w.id });
    samples.personal_sent = await b.sent();
    for (const [cmd, value] of Object.entries(samples)) {
      expect(conforms(value, shapes[cmd], cmd), cmd).toEqual([]);
    }
    expect(localDate(new Date(2026, 9, 23))).toBe("2026-10-23");
  });
});
