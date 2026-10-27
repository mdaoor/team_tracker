import { addDays, ymd } from "./format";
import {
  BridgeError,
  CATEGORIES,
  type Category,
  type DayView,
  type Draft,
  type EndOfDay,
  type ItemRow,
  type MonthDay,
  type PersonalBridge,
  type PrivacyInfo,
  type SentRow,
  type SharePolicy,
  type Suggestion,
  type TrayEntry,
  type Totals,
} from "./types";

/** An in-memory `PersonalBridge` with a fixed clock (Friday 23 Oct 2026, 15:00 Riyadh). It is what the unit tests, `pnpm dev` and the
 * Playwright audit drive; `contract.test.ts` checks every answer against the shapes the Python sidecar really produces. */
export const TODAY = "2026-10-23";
export const NOW_MIN = 15 * 60;
const TZ = "+03:00";
const CAPACITY = 384;

type Row = ItemRow & { date: string };
let seq = 0;
const iso = (date: string, hhmm: string) => `${date}T${hhmm}:00${TZ}`;

function row(
  p: Partial<Row> & { title: string; date: string; category: Category },
): Row {
  const start = p.start === undefined ? null : p.start;
  return {
    id: p.id ?? `demo-${++seq}`,
    kind: "task",
    status: "planned",
    start,
    end: null,
    all_day: start === null,
    duration_min: null,
    recurring: false,
    series_id: null,
    tags: [],
    project_hint: null,
    share_policy:
      p.category === "personal"
        ? "never"
        : p.category === "development"
          ? "never"
          : "ask",
    sent: false,
    sendable: p.category !== "personal",
    online_meeting_url: null,
    location: null,
    attendees: [],
    from_calendar: null,
    minutes_planned: p.duration_min ?? 0,
    minutes_done: 0,
    timer: null,
    elapsed_min: 0,
    ...p,
  } as Row;
}

function timed(
  date: string,
  from: string,
  to: string,
  p: Partial<Row> & { title: string; category: Category },
): Row {
  const [fh, fm] = from.split(":").map(Number);
  const [th, tm] = to.split(":").map(Number);
  const dur = th * 60 + tm - (fh * 60 + fm);
  return row({
    ...p,
    date,
    start: iso(date, from),
    end: iso(date, to),
    duration_min: dur,
    minutes_planned: dur,
    all_day: false,
  });
}

export function seed(): Row[] {
  seq = 0;
  const out: Row[] = [
    timed(TODAY, "07:00", "07:30", {
      id: "run@2026-10-23",
      title: "run",
      kind: "sport",
      category: "personal",
      recurring: true,
      series_id: "run",
      status: "done",
      minutes_done: 30,
    }),
    timed(TODAY, "09:00", "10:00", {
      id: "m-sprint",
      title: "Sprint planning",
      kind: "meeting",
      category: "meeting",
      from_calendar: "microsoft",
      online_meeting_url: "https://teams.microsoft.com/l/meetup-join/demo",
      attendees: ["sara@acme.com", "ali@acme.com"],
      minutes_done: 60,
    }),
    timed(TODAY, "11:00", "11:30", {
      id: "m-client",
      title: "Client sync with Globex",
      kind: "meeting",
      category: "meeting",
      from_calendar: "google",
      attendees: ["jo@globex.com"],
      minutes_done: 30,
    }),
    timed(TODAY, "12:00", "12:45", {
      id: "m-offsite",
      title: "Offsite logistics",
      kind: "meeting",
      category: "meeting",
      from_calendar: "google",
      minutes_done: 45,
    }),
    timed(TODAY, "15:30", "17:00", {
      id: "t-login",
      title: "fix login",
      category: "work",
      project_hint: "WEB",
    }),
    timed(TODAY, "18:00", "19:00", {
      id: "arabic@2026-10-23",
      title: "Arabic course",
      kind: "course",
      category: "development",
      recurring: true,
      series_id: "arabic",
    }),
    row({
      id: "a-read@2026-10-23",
      title: "read",
      kind: "reading",
      category: "development",
      date: TODAY,
      duration_min: 20,
      minutes_planned: 20,
      recurring: true,
      series_id: "read",
      tags: ["development"],
    }),
    row({
      id: "a-rust",
      title: "Rust ch.3",
      kind: "course",
      category: "development",
      date: TODAY,
      duration_min: 60,
      minutes_planned: 60,
      status: "done",
      minutes_done: 60,
      tags: ["development"],
    }),
  ];
  for (const date of [
    "2026-10-26",
    "2026-10-27",
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
  ]) {
    out.push(
      timed(date, "07:00", "07:30", {
        id: `run@${date}`,
        title: "run",
        kind: "sport",
        category: "personal",
        recurring: true,
        series_id: "run",
      }),
    );
  }
  out.push(
    timed("2026-10-26", "09:00", "10:00", {
      id: "m-sprint-2",
      title: "Sprint planning",
      kind: "meeting",
      category: "meeting",
      from_calendar: "microsoft",
      online_meeting_url: "https://teams.microsoft.com/l/meetup-join/demo",
      attendees: ["sara@acme.com"],
    }),
  );
  out.push(
    timed("2026-10-27", "18:00", "19:00", {
      id: "arabic@2026-10-27",
      title: "Arabic course",
      kind: "course",
      category: "development",
      recurring: true,
      series_id: "arabic",
    }),
  );
  out.push(
    timed("2026-10-15", "18:00", "19:00", {
      id: "arabic@2026-10-15",
      title: "Arabic course",
      kind: "course",
      category: "development",
      recurring: true,
      series_id: "arabic",
      status: "done",
      minutes_done: 60,
    }),
  );
  return out;
}

const minutesOf = (iso_: string | null) =>
  iso_ ? Number(iso_.slice(11, 13)) * 60 + Number(iso_.slice(14, 16)) : 0;

export interface DemoOptions {
  firstRun?: boolean;
  latencyMs?: number;
}

export function demoBridge(opts: DemoOptions = {}): PersonalBridge & {
  calls: string[];
  state: { rows: Row[]; sent: SentRow[] };
} {
  const state = { rows: seed(), sent: [] as SentRow[] };
  const calls: string[] = [];
  let timer: { id: string; since: number } | null = null;
  let tick = 0; // minutes added to the running timer by each call, so views move
  let privacy: PrivacyInfo = {
    stays_on_device:
      "Your personal items stay on this device. Meetings and work you choose to send go to Team Tracker.",
    store: "~/.local/share/tt/personal.db",
    encrypted: true,
    key_store: "keyring",
    categories: {
      work: "ask",
      meeting: "ask",
      development: "off",
      personal: "never",
    },
    assist: "off",
    assist_note:
      "off: quick-add lines never leave this device. on: a line the parser cannot place may be sent to your [llm] gateway (never to the Team Tracker server).",
    first_run_done: !opts.firstRun,
    calendars: ["google", "microsoft"],
  };
  const log = (name: string) => calls.push(name);
  const wait = async <T>(v: T): Promise<T> => {
    if (opts.latencyMs) await new Promise((r) => setTimeout(r, opts.latencyMs));
    return structuredClone(v);
  };
  const find = (id: string) => {
    const r = state.rows.find((x) => x.id === id);
    if (!r) throw new BridgeError("bad_request", `no such item: ${id}`);
    return r;
  };
  const clean = (r: Row): ItemRow => {
    const { date: _date, ...rest } = r;
    void _date;
    const running = timer?.id === r.id;
    return {
      ...rest,
      timer: running ? "running" : r.timer,
      elapsed_min: running ? r.elapsed_min + tick : r.elapsed_min,
    };
  };
  const totals = (rows: Row[]): Totals => {
    const t = Object.fromEntries(
      CATEGORIES.map((c) => [c, { planned: 0, done: 0 }]),
    ) as Totals;
    for (const r of rows) {
      if (r.status !== "skipped" && r.status !== "cancelled")
        t[r.category].planned += r.minutes_planned;
      const held =
        r.kind === "meeting" &&
        r.status === "planned" &&
        (r.date < TODAY ||
          (r.date === TODAY && minutesOf(r.end) + 2 <= NOW_MIN));
      t[r.category].done += r.status === "done" || held ? r.minutes_done : 0;
    }
    return t;
  };
  const dayView = (date: string): DayView => {
    const rows = state.rows.filter((r) => r.date === date);
    const planned = rows
      .filter((r) => r.status !== "skipped" && r.status !== "cancelled")
      .reduce((a, r) => a + r.minutes_planned, 0);
    const wd = ymd(date).wd;
    const cap = wd === 0 || wd === 6 ? 0 : CAPACITY;
    return {
      date,
      tz: "Asia/Riyadh",
      timeline: rows
        .filter((r) => !r.all_day)
        .sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""))
        .map(clean),
      anytime: rows.filter((r) => r.all_day).map(clean),
      server_items:
        date === TODAY
          ? [
              {
                key: "WEB-142",
                title: "fix the login redirect loop",
                project: "WEB",
                server: true,
              },
            ]
          : [],
      totals: totals(rows),
      planned_min: planned,
      capacity_min: cap,
      load_pct: cap ? Math.round((100 * planned) / cap) : planned ? 100 : 0,
      timer: timer
        ? { item_id: timer.id, elapsed_min: find(timer.id).elapsed_min + tick }
        : null,
    };
  };
  const suggestion = (r: Row): Suggestion => {
    const known =
      r.project_hint ??
      (r.title.includes("Globex")
        ? "DATA"
        : r.title.includes("Offsite")
          ? null
          : r.category === "meeting"
            ? "WEB"
            : null);
    const conf = r.project_hint
      ? 0.95
      : r.title.includes("Globex")
        ? 0.8
        : known
          ? 0.7
          : 0;
    return {
      project: known,
      category: r.category,
      share_policy: r.share_policy,
      confidence: conf,
      cue: conf >= 0.8 ? "✓" : conf >= 0.6 ? "suggested" : "ask",
      source: known ? "team" : null,
      reasons: known
        ? ["attendees are on team web-squad"]
        : ["no hint matched: pick a project"],
      alternatives: known
        ? [known, "DATA", "OPS"].slice(0, 3)
        : ["WEB", "DATA", "OPS"],
      rule_id: null,
    };
  };
  const trayEntries = (): TrayEntry[] =>
    state.rows
      .filter(
        (r) =>
          !r.sent &&
          r.category !== "personal" &&
          r.status !== "skipped" &&
          r.status !== "cancelled" &&
          r.date <= TODAY,
      )
      .filter((r) =>
        r.kind === "meeting"
          ? minutesOf(r.end) + 2 <= NOW_MIN
          : r.status === "done" &&
            (r.category !== "development" ||
              privacy.categories.development === "on"),
      )
      .map((r) => {
        const sug = suggestion(r);
        const minutes = r.minutes_done || r.minutes_planned;
        return {
          id: r.id,
          title: r.title,
          kind: r.kind,
          category: r.category,
          start: r.start,
          end: r.end,
          minutes,
          note: r.attendees.length
            ? `${r.title} with ${r.attendees.map((a) => "@" + a.split("@")[0]).join(", ")}`
            : r.title,
          due_at: r.end ?? iso(TODAY, "15:00"),
          auto: false,
          auto_sends: `Meeting · ${minutes >= 60 ? `${minutes / 60} h` : `${minutes} min`}`,
          online_meeting_url: r.online_meeting_url,
          suggestion: sug,
          needs_project: sug.project === null && r.category !== "development",
          label: `Send ${minutes >= 60 ? `${minutes / 60} h` : `${minutes} min`} ${r.title}${sug.project ? ` → ${sug.project}` : ""}`,
        };
      })
      .sort((a, b) => b.due_at.localeCompare(a.due_at));
  const sentView = () => state.sent.map((s) => ({ ...s }));

  const bridge: PersonalBridge = {
    day: async (date) => (log("day"), wait(dayView(date ?? TODAY))),
    week: async (date) => {
      log("week");
      const d = date ?? TODAY;
      const start = addDays(d, -((ymd(d).wd + 6) % 7));
      const days = Array.from({ length: 7 }, (_, i) =>
        dayView(addDays(start, i)),
      );
      const rows = state.rows.filter(
        (r) => r.date >= start && r.date <= addDays(start, 6),
      );
      return wait({
        week_start: start,
        days,
        totals: totals(rows),
        planned_min: days.reduce((a, x) => a + x.planned_min, 0),
        capacity_min: days.reduce((a, x) => a + x.capacity_min, 0),
      });
    },
    month: async (year = 2026, month = 10) => {
      log("month");
      const pad = (n: number) => String(n).padStart(2, "0");
      const first = `${year}-${pad(month)}-01`;
      const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
      const days: Record<string, MonthDay> = {};
      for (const r of state.rows.filter((x) =>
        x.date.startsWith(`${year}-${pad(month)}`),
      )) {
        const d = (days[r.date] ??= {
          dots: [],
          minutes: {},
          sent_min: 0,
          private_min: 0,
        });
        if (!d.dots.includes(r.category)) d.dots.push(r.category);
        const m = r.minutes_done || r.minutes_planned;
        d.minutes[r.category] = (d.minutes[r.category] ?? 0) + m;
        if (r.sent) d.sent_min += m;
        else d.private_min += m;
      }
      const lead = (ymd(first).wd + 6) % 7;
      const cells = Array.from(
        { length: Math.ceil((lead + last) / 7) * 7 },
        (_, i) => addDays(first, i - lead),
      );
      const weeks = Array.from({ length: cells.length / 7 }, (_, w) =>
        cells.slice(w * 7, w * 7 + 7),
      );
      const totals_h = Object.fromEntries(
        CATEGORIES.map((c) => [
          c,
          Math.round(
            Object.values(days).reduce((a, d) => a + (d.minutes[c] ?? 0), 0) /
              6,
          ) / 10,
        ]),
      ) as Record<Category, number>;
      return wait({
        year,
        month,
        weeks,
        days,
        totals_h,
        sparkline: Object.entries(days).map(([date, d]) => ({
          date,
          sent: d.sent_min,
          private: d.private_min,
        })),
      });
    },
    preview: async (line) => {
      log("preview");
      const rec = /every weekday|daily|tue\/thu/i.test(line);
      const cat: Category = /^course:|read|training/i.test(line)
        ? "development"
        : /run|gym|yoga/i.test(line)
          ? "personal"
          : "work";
      const draft: Draft = {
        title:
          line
            .replace(/^\w+:\s*/, "")
            .replace(/\s+(every .*|daily|\d+[hm]\b.*)$/i, "")
            .trim() || line,
        kind:
          cat === "development"
            ? "course"
            : cat === "personal"
              ? "sport"
              : "task",
        category: cat,
        start: iso(TODAY, "18:00"),
        end: null,
        duration_min: 30,
        all_day: false,
        rrule: rec ? "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" : null,
        rrule_text: rec ? "every weekday at 07:00" : null,
        tags: cat === "development" ? ["development"] : [],
        project_hint: null,
        warnings: [],
        notes: "",
        kind_source: "keyword",
        category_source: "kind",
        suggested_kind: null,
        suggested_category: null,
      };
      return wait({ drafts: line.trim() ? [draft] : [] });
    },
    add: async (line) => {
      log("add");
      if (!line.trim())
        throw new BridgeError(
          "bad_request",
          "nothing to add: try `run 30m at 6pm`",
        );
      const cat: Category = /^course:|read|training/i.test(line)
        ? "development"
        : /run|gym|yoga/i.test(line)
          ? "personal"
          : "work";
      const dur = Number(/(\d+)\s*m\b/.exec(line)?.[1] ?? 30);
      const r = row({
        title: line.replace(/^\w+:\s*/, "").replace(/\s+\d+\s*m\b.*$/i, ""),
        date: TODAY,
        category: cat,
        duration_min: dur,
        minutes_planned: dur,
      });
      state.rows.push(r);
      return wait({ items: [clean(r)] });
    },
    recurAdd: async (text) => {
      log("recurAdd");
      if (!/every|daily|\//i.test(text))
        throw new BridgeError(
          "bad_request",
          "no recurrence found: try `every weekday 7:00 run 30m`",
        );
      const r = row({
        title: text.replace(/^every \w+ [\d:]+ /i, ""),
        date: TODAY,
        category: "personal",
        recurring: true,
      });
      state.rows.push(r);
      return wait({
        id: r.id,
        title: r.title,
        rule: "every weekday at 07:00",
        rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        category: "personal" as Category,
      });
    },
    complete: async (id, minutes) => {
      log("complete");
      const r = find(id);
      if (timer?.id === id) timer = null;
      r.status = "done";
      r.minutes_done = minutes ?? r.minutes_planned;
      return wait({ item: clean(r) });
    },
    skip: async (id) => (
      log("skip"),
      (find(id).status = "skipped"),
      wait({ item: clean(find(id)) })
    ),
    reopen: async (id) => (
      log("reopen"),
      (find(id).status = "planned"),
      wait({ item: clean(find(id)) })
    ),
    remove: async (id) => {
      log("remove");
      const n = state.rows.length;
      state.rows = state.rows.filter((r) => r.id !== id);
      return wait({ deleted: state.rows.length < n });
    },
    reschedule: async (id, start) => {
      log("reschedule");
      const r = find(id);
      const dur = r.duration_min ?? 0;
      r.date = start.slice(0, 10);
      r.start = start;
      const end = minutesOf(start) + dur;
      r.end = dur
        ? iso(
            r.date,
            `${String(Math.floor(end / 60)).padStart(2, "0")}:${String(end % 60).padStart(2, "0")}`,
          )
        : null;
      r.all_day = false;
      return wait({ item: clean(r) });
    },
    setCategory: async (id, category) => {
      log("setCategory");
      const r = find(id);
      r.category = category;
      r.sendable = category !== "personal";
      r.share_policy =
        category === "work" || category === "meeting" ? "ask" : "never";
      return wait({ item: clean(r) });
    },
    timerStart: async ({ id, title }) => {
      log("timerStart");
      let r = id ? find(id) : undefined;
      if (!r) {
        r = row({ title: title ?? "untitled", date: TODAY, category: "work" });
        state.rows.push(r);
      }
      const paused = timer && timer.id !== r.id ? timer.id : undefined;
      if (paused) find(paused).timer = "paused";
      timer = { id: r.id, since: tick };
      r.timer = "running";
      r.status = "active";
      return wait({
        item: r.id,
        title: r.title,
        state: "running",
        ...(paused ? { paused } : {}),
      });
    },
    timerStop: async () => {
      log("timerStop");
      if (!timer) return wait({ stopped: null });
      const r = find(timer.id);
      const minutes = r.elapsed_min + tick;
      r.timer = "stopped";
      r.status = "planned";
      r.elapsed_min = minutes;
      timer = null;
      return wait({ stopped: { item: r.id, title: r.title, minutes } });
    },
    timerStatus: async () => (
      log("timerStatus"),
      wait({
        timer: timer
          ? {
              item: timer.id,
              title: find(timer.id).title,
              elapsed_min: find(timer.id).elapsed_min + tick,
              state: "running",
            }
          : null,
      })
    ),
    tray: async () => (log("tray"), wait({ entries: trayEntries() })),
    send: async ({ id, project, minutes }) => {
      log("send");
      const r = find(id);
      if (r.category === "personal")
        throw new BridgeError("not_sendable", "personal items are never sent");
      const target =
        r.category === "development"
          ? "DEV"
          : (project ?? r.project_hint ?? suggestion(r).project);
      if (!target)
        throw new BridgeError(
          "needs_project",
          "choose a project (WEB, DATA, OPS)",
          ["WEB", "DATA", "OPS"],
        );
      r.sent = true;
      r.project_hint = r.category === "development" ? r.project_hint : target;
      const mins = minutes ?? (r.minutes_done || r.minutes_planned);
      state.sent.unshift({
        item: r.id,
        title: r.title,
        client_id: `c-${r.id}`,
        server_key: null,
        project: target,
        minutes: mins,
        at: iso(TODAY, "15:00"),
        unsent: false,
        can_unsend: true,
      });
      return wait({
        item: r.id,
        client_id: `c-${r.id}`,
        project: target,
        minutes: mins,
        tags: r.category === "development" ? ["development"] : [],
      });
    },
    notWork: async (id) => (
      log("notWork"),
      (find(id).status = "skipped"),
      (find(id).category = "personal"),
      wait({ item: clean(find(id)) })
    ),
    unsend: async (id) => {
      log("unsend");
      const r = find(id);
      r.sent = false;
      for (const s of state.sent)
        if (s.item === id) ((s.unsent = true), (s.can_unsend = false));
      return wait({ how: "discarded" as const });
    },
    rememberShare: async (category, mode) => {
      log("rememberShare");
      if (category === "development")
        privacy.categories.development = mode === "never" ? "off" : "on";
      else if (category === "work" || category === "meeting")
        privacy.categories[category] = mode as "ask";
      return wait(privacy);
    },
    eod: async (date) => {
      log("eod");
      const d = dayView(date ?? TODAY);
      const t = d.totals;
      const unsent = trayEntries().filter((e) =>
        (e.start ?? "").startsWith(date ?? TODAY),
      );
      const e: EndOfDay = {
        date: d.date,
        hours_by_category: Object.fromEntries(
          CATEGORIES.map((c) => [c, Math.round(t[c].done / 6) / 10]),
        ) as Record<Category, number>,
        planned_by_category: Object.fromEntries(
          CATEGORIES.map((c) => [c, Math.round(t[c].planned / 6) / 10]),
        ) as Record<Category, number>,
        done_count: state.rows.filter(
          (r) => r.date === d.date && r.status === "done",
        ).length,
        open_count: state.rows.filter(
          (r) =>
            r.date === d.date &&
            (r.status === "planned" || r.status === "active") &&
            r.kind !== "meeting",
        ).length,
        unsent,
        sent_count: state.rows.filter((r) => r.date === d.date && r.sent)
          .length,
      };
      return wait(e);
    },
    eodSendAll: async () => {
      log("eodSendAll");
      const sent = [];
      const needs: string[] = [];
      for (const e of trayEntries()) {
        if (e.needs_project) needs.push(e.id);
        else sent.push(await bridge.send({ id: e.id }));
      }
      return wait({ sent, needs_project: needs });
    },
    eodConfirm: async () => {
      log("eodConfirm");
      return wait(
        state.sent.some((s) => !s.unsent)
          ? {
              confirmed: true,
              minutes_total: state.sent.reduce(
                (a, s) => a + (s.minutes ?? 0),
                0,
              ),
            }
          : { confirmed: false, reason: "nothing was sent today" },
      );
    },
    weeklyReview: async () => {
      log("weeklyReview");
      return wait({
        week_start: "2026-10-19",
        planned_h: 18.5,
        done_h: 14.0,
        completion_pct: 76,
        development_h: 3.5,
        meetings_h: 6.0,
        meeting_load_pct: 31,
        streaks: [
          { series_id: "run", title: "run", streak: 4, done: 9, of: 10 },
          { series_id: "read", title: "read", streak: 0, done: 5, of: 12 },
        ],
        development_items: [
          { title: "Arabic course", hours: 2 },
          { title: "Rust ch.3", hours: 1.5 },
        ],
      });
    },
    developmentSummary: async () => (
      log("developmentSummary"),
      wait({
        client_id: "note-1",
        note: "Week of 2026-10-19: 3.5 h development: Arabic course (2 h), Rust ch.3 (1.5 h)",
      })
    ),
    deliver: async () => (
      log("deliver"),
      wait({
        sent: state.sent.filter((s) => !s.unsent).length,
        offline: false,
        kept: 0,
        failed: 0,
      })
    ),
    privacy: async () => (log("privacy"), wait(privacy)),
    firstRun: async ({ work, meeting, development, assist }) => {
      log("firstRun");
      privacy = {
        ...privacy,
        first_run_done: true,
        assist: assist ? "on" : "off",
        categories: {
          work: work as "ask",
          meeting: meeting as "ask",
          development: development ? "on" : "off",
          personal: "never",
        },
      };
      return wait(privacy);
    },
    sent: async () => (log("sent"), wait({ sent: sentView() })),
    eraseLocal: async () => (
      log("eraseLocal"),
      (state.rows = []),
      wait({
        files: ["personal.db"],
        secrets: ["personal-store-key", "oauth:google"],
      })
    ),
  };
  return Object.assign(bridge, { calls, state }) as PersonalBridge & {
    calls: string[];
    state: { rows: Row[]; sent: SentRow[] };
  };
}

export type { SharePolicy };
