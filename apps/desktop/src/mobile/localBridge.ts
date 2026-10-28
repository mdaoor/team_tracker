import { addDays, ymd } from "../personal/format";
import {
  BridgeError,
  CATEGORIES,
  type CalendarSettings,
  type Category,
  type DayView,
  type Draft,
  type EndOfDay,
  type ItemRow,
  type MonthDay,
  type MonthView,
  type PersonalBridge,
  type PrivacyInfo,
  type SentRow,
  type Status,
  type Suggestion,
  type Totals,
  type TrayEntry,
  type WeekView,
  type WeeklyReview,
} from "../personal/types";

/** The phone's `PersonalBridge` ("Mobile Lite", ADR-0057): the React views of the desktop planner run in a browser/PWA against *this* instead
 * of the Python sidecar. It is a deliberately small planner kept in `localStorage`: add one-liners, a timer, Today/Week/Month, the
 * ready-to-send tray and an offline outbox that is delivered to the server when it is reachable. It is NOT the desktop engine:
 * no recurring items, no calendars, no learned rules or suggestions, no encryption at rest (browser storage), no OS notifications.
 * What it cannot do answers with a `not_on_mobile` BridgeError that says where to do it, never with a silent no-op. */

export const STORAGE_KEY = "tt.mobile.v1";
const CAPACITY_DEFAULT_H = 40;
const NOT_ON_MOBILE = "not_on_mobile";

export interface MobileSettings {
  /** "@handle" the batch is sent as. */
  handle: string;
  workspace: string;
  /** Origin of the Team Tracker server; empty = the page's own origin (the usual same-origin deployment). */
  server: string;
  /** A capture-scoped API token. Stored in this browser, in clear: the page says so. */
  token: string;
  /** The project keys offered as chips when an item has none. */
  projects: string[];
  devProject: string;
  capacityHoursPerWeek: number;
}

interface Item {
  id: string;
  title: string;
  kind: string;
  category: Category;
  date: string;
  start: string | null;
  end: string | null;
  all_day: boolean;
  duration_min: number | null;
  status: Status;
  tags: string[];
  project_hint: string | null;
  sent: boolean;
  minutes_done: number;
  timer: ItemRow["timer"];
  timer_acc: number;
  timer_since: number | null;
}
interface OutboxEntry {
  client_id: string;
  item: string;
  batch: Record<string, unknown>;
  delivered: boolean;
  error?: string;
}
interface State {
  v: 1;
  items: Item[];
  sent: SentRow[];
  outbox: OutboxEntry[];
  settings: MobileSettings;
  privacy: {
    work: "ask" | "never";
    meeting: "ask" | "never";
    development: boolean;
    first_run_done: boolean;
  };
  notified: string[];
}

const blank = (): State => ({
  v: 1,
  items: [],
  sent: [],
  outbox: [],
  settings: {
    handle: "",
    workspace: "",
    server: "",
    token: "",
    projects: [],
    devProject: "DEV",
    capacityHoursPerWeek: CAPACITY_DEFAULT_H,
  },
  privacy: {
    work: "ask",
    meeting: "ask",
    development: false,
    first_run_done: false,
  },
  notified: [],
});

// ---------------------------------------------------------------------------------------------------------------- time
const pad = (n: number) => String(n).padStart(2, "0");
export const localDate = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export function isoLocal(d: Date): string {
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  return `${localDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}${sign}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`;
}
function at(date: string, minutes: number): Date {
  const { y, m, d } = ymd(date);
  return new Date(y, m - 1, d, Math.floor(minutes / 60), minutes % 60, 0);
}
const mondayOf = (d: string) => addDays(d, -((ymd(d).wd + 6) % 7));
const tenth = (min: number) => Math.round(min / 6) / 10;
const dur = (m: number) =>
  m >= 60
    ? m % 60
      ? `${Math.floor(m / 60)} h ${m % 60} min`
      : `${m / 60} h`
    : `${m} min`;

function newId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// ------------------------------------------------------------------------------------------------------------ quick add
export interface Parsed extends Draft {
  date: string;
  repeats: boolean;
}
const RECUR =
  /\b(every|daily|weekly|weekdays?|weekends?|mon\/|tue\/|until\b|for\s+\d+\s+weeks?|x\d+\b)/i;
const DURATION =
  /\b(\d+(?:\.\d+)?)\s*h(?:ours?|rs?)?(?:\s*(\d+)\s*m(?:in(?:utes?)?)?\b|(\d{2})\b)?|\b(\d+)\s*m(?:in(?:utes?)?)?\b/i;
const CLOCK_AMPM = /\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/i;
const CLOCK_AT = /\bat\s+(\d{1,2})(?::(\d{2}))?\b/i;
const CLOCK_24 = /\b(\d{1,2}):(\d{2})\b/;
const PREFIX: Record<string, Category> = {
  work: "work",
  meeting: "meeting",
  course: "development",
  dev: "development",
  development: "development",
  personal: "personal",
};

/** One line -> a draft. Deliberately small and predictable: `[work:|meeting:|course:|personal:] title [p:KEY] [#tag] [30m|1h30] [at 6pm|18:00]
 * [today|tomorrow]`. A line it cannot place is personal (the safe default, same as the desktop). */
export function parseLine(line: string, now: Date): Parsed {
  let rest = line.trim();
  const warnings: string[] = [];
  let category: Category = "personal";
  let categorySource: Draft["category_source"] = "kind";
  let kindSource: Draft["kind_source"] = "default";
  let kind = "task";
  const prefix = /^(\w+):\s*/.exec(rest);
  if (prefix && PREFIX[prefix[1].toLowerCase()]) {
    category = PREFIX[prefix[1].toLowerCase()];
    kindSource = "label";
    rest = rest.slice(prefix[0].length);
    if (category === "development") kind = "course";
    if (category === "meeting") kind = "meeting";
  }
  const tags: string[] = [];
  for (const t of rest.matchAll(/(?:^|\s)#([\w-]+)/g))
    tags.push(t[1].toLowerCase());
  rest = rest.replace(/(?:^|\s)#[\w-]+/g, " ");
  if (tags.includes("development")) {
    category = "development";
    categorySource = "tag";
  }
  let project: string | null = null;
  const proj = /(?:^|\s)p:([A-Za-z][\w-]*)/.exec(rest);
  if (proj) {
    project = proj[1].toUpperCase();
    rest = rest.replace(proj[0], " ");
    if (category === "personal") category = "work";
  }
  const repeats = RECUR.test(rest);
  if (repeats)
    warnings.push(
      "Repeating items are not available on the phone yet: add this one on the desktop.",
    );
  let minutes: number | null = null;
  const d = DURATION.exec(rest);
  if (d) {
    minutes = d[4]
      ? Number(d[4])
      : Math.round(Number(d[1]) * 60) + Number(d[2] ?? d[3] ?? 0);
    rest = rest.replace(d[0], " ");
  }
  let clock: number | null = null;
  const ap = CLOCK_AMPM.exec(rest);
  const a24 = CLOCK_24.exec(rest);
  const aat = CLOCK_AT.exec(rest);
  if (ap) {
    clock =
      (Number(ap[1]) % 12) * 60 +
      Number(ap[2] ?? 0) +
      (ap[3].toLowerCase() === "pm" ? 720 : 0);
    rest = rest.replace(ap[0], " ");
  } else if (a24) {
    clock = Number(a24[1]) * 60 + Number(a24[2]);
    rest = rest.replace(a24[0], " ");
  } else if (aat) {
    clock = Number(aat[1]) * 60 + Number(aat[2] ?? 0);
    rest = rest.replace(aat[0], " ");
  }
  if (clock !== null && clock >= 1440) {
    warnings.push("That time of day does not exist: left the item anytime.");
    clock = null;
  }
  let date = localDate(now);
  const day = /\b(today|tomorrow)\b/i.exec(rest);
  if (day) {
    if (day[1].toLowerCase() === "tomorrow") date = addDays(date, 1);
    rest = rest.replace(day[0], " ");
  }
  const title = rest.replace(/\s+/g, " ").replace(/^[\s,-]+|[\s,-]+$/g, "");
  if (!title)
    throw new BridgeError(
      "bad_request",
      "nothing to add: try `run 30m at 6pm`",
    );
  if (category === "personal" && /\b(run|gym|yoga|walk|swim)\b/i.test(title))
    kind = "sport";
  const start = clock === null ? null : isoLocal(at(date, clock));
  const end =
    clock !== null && minutes ? isoLocal(at(date, clock + minutes)) : null;
  return {
    title,
    kind,
    category,
    start,
    end,
    duration_min: minutes,
    all_day: clock === null,
    rrule: null,
    rrule_text: null,
    tags:
      category === "development" && !tags.includes("development")
        ? [...tags, "development"]
        : tags,
    project_hint: project,
    warnings,
    notes: "",
    kind_source: kindSource,
    category_source: categorySource,
    suggested_kind: null,
    suggested_category: null,
    date,
    repeats,
  };
}

// ------------------------------------------------------------------------------------------------------------- the bridge
export interface LocalBridgeEnv {
  storage?: Storage | null;
  now?: () => Date;
  fetchFn?: typeof fetch;
}
export type LocalBridge = PersonalBridge & {
  settings(): MobileSettings;
  saveSettings(patch: Partial<MobileSettings>): MobileSettings;
  /** Messages waiting for delivery. */
  pending(): number;
  /** Called after every change that was saved; returns the unsubscribe function. */
  subscribe(listener: () => void): () => void;
};

export function localBridge(env: LocalBridgeEnv = {}): LocalBridge {
  const now = env.now ?? (() => new Date());
  const doFetch =
    env.fetchFn ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  let storage: Storage | null = null;
  try {
    storage = env.storage === undefined ? globalThis.localStorage : env.storage;
  } catch {
    storage = null; // blocked storage (private window): the session still works, it just does not persist
  }
  let st: State = load();

  function load(): State {
    try {
      const raw = storage?.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as State;
        if (parsed.v === 1)
          return {
            ...blank(),
            ...parsed,
            settings: { ...blank().settings, ...parsed.settings },
          };
      }
    } catch {
      /* unreadable: start empty rather than crash */
    }
    return blank();
  }
  const listeners = new Set<() => void>();
  const save = () => {
    listeners.forEach((l) => l());
    try {
      storage?.setItem(STORAGE_KEY, JSON.stringify(st));
    } catch {
      /* quota or blocked: the in-memory state still serves this session */
    }
  };
  const today = () => localDate(now());
  const find = (id: string): Item => {
    const it = st.items.find((x) => x.id === id);
    if (!it) throw new BridgeError("bad_request", `no such item: ${id}`);
    return it;
  };
  const elapsed = (it: Item) =>
    it.timer_acc +
    (it.timer_since === null
      ? 0
      : Math.floor((now().getTime() - it.timer_since) / 60000));
  const stopTimer = (it: Item, to: ItemRow["timer"]) => {
    it.timer_acc = elapsed(it);
    it.timer_since = null;
    it.timer = to;
  };
  const running = () => st.items.find((i) => i.timer === "running") ?? null;
  const held = (it: Item) =>
    it.kind === "meeting" &&
    it.status === "planned" &&
    it.end !== null &&
    new Date(it.end).getTime() + 120000 <= now().getTime();
  const doneMin = (it: Item) =>
    it.status === "done" || held(it)
      ? it.minutes_done || it.duration_min || 0
      : 0;
  const planned = (it: Item) =>
    it.status === "skipped" || it.status === "cancelled"
      ? 0
      : (it.duration_min ?? 0);

  const clean = (it: Item): ItemRow => ({
    id: it.id,
    title: it.title,
    kind: it.kind,
    category: it.category,
    status: it.status,
    start: it.start,
    end: it.end,
    all_day: it.all_day,
    duration_min: it.duration_min,
    recurring: false,
    series_id: null,
    tags: it.tags,
    project_hint: it.project_hint,
    share_policy:
      it.category === "work" || it.category === "meeting" ? "ask" : "never",
    sent: it.sent,
    sendable: it.category !== "personal",
    online_meeting_url: null,
    location: null,
    attendees: [],
    from_calendar: null,
    minutes_planned: it.duration_min ?? 0,
    minutes_done: it.minutes_done,
    timer: it.timer,
    elapsed_min: elapsed(it),
  });

  const totals = (items: Item[]): Totals => {
    const t = Object.fromEntries(
      CATEGORIES.map((c) => [c, { planned: 0, done: 0 }]),
    ) as Totals;
    for (const it of items) {
      t[it.category].planned += planned(it);
      t[it.category].done += doneMin(it);
    }
    return t;
  };
  const dailyCapacity = (date: string) => {
    const wd = ymd(date).wd;
    return wd === 0 || wd === 6
      ? 0
      : Math.round((st.settings.capacityHoursPerWeek * 60) / 5);
  };
  const dayView = (date: string): DayView => {
    const items = st.items.filter((i) => i.date === date);
    const plannedMin = items.reduce((a, i) => a + planned(i), 0);
    const cap = dailyCapacity(date);
    const run = running();
    return {
      date,
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      timeline: items
        .filter((i) => !i.all_day)
        .sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""))
        .map(clean),
      anytime: items.filter((i) => i.all_day).map(clean),
      server_items: [],
      totals: totals(items),
      planned_min: plannedMin,
      capacity_min: cap,
      load_pct: cap
        ? Math.round((100 * plannedMin) / cap)
        : plannedMin
          ? 100
          : 0,
      timer: run ? { item_id: run.id, elapsed_min: elapsed(run) } : null,
    };
  };

  // -- sending
  const suggestion = (it: Item): Suggestion => {
    const known = it.project_hint;
    return {
      project: known,
      category: it.category,
      share_policy:
        it.category === "work" || it.category === "meeting" ? "ask" : "never",
      confidence: known ? 0.95 : 0,
      cue: known ? "✓" : "ask",
      source: known ? "you" : null,
      reasons: known ? ["you set this project"] : ["pick a project"],
      alternatives: (known
        ? [known, ...st.settings.projects.filter((p) => p !== known)]
        : st.settings.projects
      ).slice(0, 3),
      rule_id: null,
    };
  };
  const trayItems = (): Item[] =>
    st.items.filter(
      (i) =>
        !i.sent &&
        i.category !== "personal" &&
        i.status !== "skipped" &&
        i.status !== "cancelled" &&
        i.date <= today() &&
        st.privacy[
          i.category === "development" ? "development" : i.category
        ] !== false &&
        st.privacy[
          i.category === "development" ? "development" : i.category
        ] !== "never" &&
        (i.kind === "meeting"
          ? i.end !== null &&
            new Date(i.end).getTime() + 120000 <= now().getTime()
          : i.status === "done") &&
        (i.minutes_done || i.duration_min || 0) > 0,
    );
  const trayEntry = (it: Item): TrayEntry => {
    const sug = suggestion(it);
    const minutes = it.minutes_done || it.duration_min || 0;
    return {
      id: it.id,
      title: it.title,
      kind: it.kind,
      category: it.category,
      start: it.start,
      end: it.end,
      minutes,
      note: it.title,
      due_at: it.end ?? it.start ?? isoLocal(now()),
      auto: false,
      auto_sends: `${it.kind === "meeting" ? "Meeting" : "Work"} · ${dur(minutes)}`,
      online_meeting_url: null,
      suggestion: sug,
      needs_project: sug.project === null && it.category !== "development",
      label: `Send ${dur(minutes)} ${it.title}${sug.project ? ` → ${sug.project}` : ""}`,
    };
  };
  const trayEntries = () =>
    trayItems()
      .map(trayEntry)
      .sort((a, b) => b.due_at.localeCompare(a.due_at));

  const doSend = (
    id: string,
    o: { project?: string; note?: string; minutes?: number } = {},
  ) => {
    const it = find(id);
    if (it.category === "personal")
      throw new BridgeError("not_sendable", "personal items are never sent");
    if (it.sent) throw new BridgeError("not_sendable", "already sent");
    const minutes = o.minutes ?? (it.minutes_done || it.duration_min || 0);
    if (!minutes || minutes <= 0)
      throw new BridgeError(
        "not_sendable",
        "no duration to report: set one, or run the timer",
      );
    const dev = it.category === "development";
    const project = dev
      ? st.settings.devProject
      : (o.project ?? it.project_hint);
    if (!project)
      throw new BridgeError(
        "needs_project",
        `choose a project (${st.settings.projects.join(", ") || "set your projects in Phone settings"})`,
        st.settings.projects.slice(0, 3),
      );
    if (!st.settings.handle || !st.settings.workspace)
      throw new BridgeError(
        "bad_request",
        "set your @handle and workspace in Phone settings first",
      );
    const stamp = new Date(
      Math.min(
        new Date(it.end ?? it.start ?? now()).getTime(),
        now().getTime(),
      ),
    );
    const note = o.note ?? it.title;
    const client_id = newId();
    const tags = dev
      ? [...new Set([...it.tags, "development"])].sort()
      : it.tags.filter((t) => t !== "development");
    const message = {
      actor: st.settings.handle,
      action: "log_time",
      at: stamp.toISOString(),
      client_id,
      item: { title: it.title },
      project,
      tags,
      note,
      minutes,
      raw_input: `${it.kind} · ${dur(minutes)} · ${note}`,
      source: { origin: "personal" },
    };
    st.outbox.push({
      client_id,
      item: it.id,
      delivered: false,
      batch: {
        workspace: st.settings.workspace,
        messages: [message],
        client_version: "0.2",
      },
    });
    it.sent = true;
    st.sent.unshift({
      item: it.id,
      title: it.title,
      client_id,
      server_key: null,
      project,
      minutes,
      at: isoLocal(now()),
      unsent: false,
      can_unsend: true,
    });
    save();
    return { item: it.id, client_id, project, minutes, tags };
  };

  const notOnMobile = (what: string, where: string): never => {
    throw new BridgeError(
      NOT_ON_MOBILE,
      `${what} is not available on the phone yet: ${where}.`,
    );
  };

  const privacyInfo = (): PrivacyInfo => ({
    stays_on_device:
      "Your personal items stay in this browser. Work and meetings you choose to send go to Team Tracker when the phone is online.",
    store: "this browser (localStorage)",
    encrypted: false,
    key_store: "none",
    categories: {
      work: st.privacy.work,
      meeting: st.privacy.meeting,
      development: st.privacy.development ? "on" : "off",
      personal: "never",
    },
    assist: "off",
    assist_note:
      "Not available on the phone: quick-add lines never leave this device and are never sent to a model.",
    first_run_done: st.privacy.first_run_done,
    calendars: [],
  });

  const bridge: LocalBridge = {
    settings: () => ({ ...st.settings, projects: [...st.settings.projects] }),
    saveSettings: (patch) => {
      st.settings = {
        ...st.settings,
        ...patch,
        projects: (patch.projects ?? st.settings.projects)
          .map((p) => p.trim().toUpperCase())
          .filter(Boolean),
      };
      save();
      return bridge.settings();
    },
    pending: () => st.outbox.filter((o) => !o.delivered).length,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    day: async (date) => dayView(date ?? today()),
    week: async (date) => {
      const start = mondayOf(date ?? today());
      const days = Array.from({ length: 7 }, (_, i) =>
        dayView(addDays(start, i)),
      );
      const items = st.items.filter(
        (i) => i.date >= start && i.date <= addDays(start, 6),
      );
      const w: WeekView = {
        week_start: start,
        days,
        totals: totals(items),
        planned_min: days.reduce((a, d) => a + d.planned_min, 0),
        capacity_min: days.reduce((a, d) => a + d.capacity_min, 0),
      };
      return w;
    },
    month: async (year, month) => {
      const base = ymd(today());
      const y = year ?? base.y;
      const m = month ?? base.m;
      const first = `${y}-${pad(m)}-01`;
      const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
      const days: Record<string, MonthDay> = {};
      for (const it of st.items.filter((x) =>
        x.date.startsWith(`${y}-${pad(m)}`),
      )) {
        const d = (days[it.date] ??= {
          dots: [],
          minutes: {},
          sent_min: 0,
          private_min: 0,
        });
        if (!d.dots.includes(it.category)) d.dots.push(it.category);
        const mins = doneMin(it) || (it.duration_min ?? 0);
        d.minutes[it.category] = (d.minutes[it.category] ?? 0) + mins;
        if (it.sent) d.sent_min += mins;
        else d.private_min += mins;
      }
      const lead = (ymd(first).wd + 6) % 7;
      const cells = Array.from(
        { length: Math.ceil((lead + last) / 7) * 7 },
        (_, i) => addDays(first, i - lead),
      );
      const weeks = Array.from({ length: cells.length / 7 }, (_, w) =>
        cells.slice(w * 7, w * 7 + 7),
      );
      const v: MonthView = {
        year: y,
        month: m,
        weeks,
        days,
        totals_h: Object.fromEntries(
          CATEGORIES.map((c) => [
            c,
            tenth(
              Object.values(days).reduce((a, d) => a + (d.minutes[c] ?? 0), 0),
            ),
          ]),
        ) as Record<Category, number>,
        sparkline: Object.entries(days).map(([date, d]) => ({
          date,
          sent: d.sent_min,
          private: d.private_min,
        })),
      };
      return v;
    },
    preview: async (line) => {
      if (!line.trim()) return { drafts: [] };
      try {
        const { date: _d, repeats: _r, ...draft } = parseLine(line, now());
        void _d;
        void _r;
        return { drafts: [draft] };
      } catch (e) {
        if (e instanceof BridgeError) return { drafts: [] };
        throw e;
      }
    },
    add: async (line) => {
      const p = parseLine(line, now());
      if (p.repeats) notOnMobile("Repeating items", "add them on the desktop");
      const it: Item = {
        id: newId(),
        title: p.title,
        kind: p.kind,
        category: p.category,
        date: p.date,
        start: p.start,
        end: p.end,
        all_day: p.all_day,
        duration_min: p.duration_min,
        status: "planned",
        tags: p.tags,
        project_hint: p.project_hint,
        sent: false,
        minutes_done: 0,
        timer: null,
        timer_acc: 0,
        timer_since: null,
      };
      st.items.push(it);
      save();
      return { items: [clean(it)] };
    },
    recurAdd: async () =>
      notOnMobile("Repeating items", "add them on the desktop"),
    complete: async (id, minutes) => {
      const it = find(id);
      if (it.timer === "running" || it.timer === "paused")
        stopTimer(it, "stopped");
      it.status = "done";
      it.minutes_done =
        minutes ?? (it.timer_acc || it.minutes_done || it.duration_min || 0);
      save();
      return { item: clean(it) };
    },
    skip: async (id) => {
      const it = find(id);
      if (it.timer === "running") stopTimer(it, "stopped");
      it.status = "skipped";
      save();
      return { item: clean(it) };
    },
    reopen: async (id) => {
      const it = find(id);
      it.status = "planned";
      save();
      return { item: clean(it) };
    },
    remove: async (id) => {
      const n = st.items.length;
      st.items = st.items.filter((i) => i.id !== id);
      save();
      return { deleted: st.items.length < n };
    },
    reschedule: async (id, start) => {
      const it = find(id);
      const s = new Date(start);
      const minutes = it.duration_min ?? 0;
      it.date = localDate(s);
      it.start = isoLocal(s);
      it.end = minutes
        ? isoLocal(new Date(s.getTime() + minutes * 60000))
        : null;
      it.all_day = false;
      save();
      return { item: clean(it) };
    },
    setCategory: async (id, category) => {
      const it = find(id);
      it.category = category;
      save();
      return { item: clean(it) };
    },
    timerStart: async ({ id, title }) => {
      let it = id ? find(id) : undefined;
      if (!it) {
        const p = parseLine(title ?? "untitled", now());
        it = {
          id: newId(),
          title: p.title,
          kind: p.kind,
          category: p.category === "personal" ? "work" : p.category,
          date: today(),
          start: null,
          end: null,
          all_day: true,
          duration_min: null,
          status: "planned",
          tags: p.tags,
          project_hint: p.project_hint,
          sent: false,
          minutes_done: 0,
          timer: null,
          timer_acc: 0,
          timer_since: null,
        };
        st.items.push(it);
      }
      const other = running();
      const paused = other && other.id !== it.id ? other.id : undefined;
      if (other && paused) stopTimer(other, "paused");
      it.timer = "running";
      it.timer_since = now().getTime();
      it.status = "active";
      save();
      return {
        item: it.id,
        title: it.title,
        state: "running",
        ...(paused ? { paused } : {}),
      };
    },
    timerStop: async () => {
      const it = running();
      if (!it) return { stopped: null };
      stopTimer(it, "stopped");
      it.status = "planned";
      it.minutes_done = it.timer_acc;
      save();
      return {
        stopped: { item: it.id, title: it.title, minutes: it.timer_acc },
      };
    },
    timerStatus: async () => {
      const it = running();
      return {
        timer: it
          ? {
              item: it.id,
              title: it.title,
              elapsed_min: elapsed(it),
              state: "running",
            }
          : null,
      };
    },
    tray: async () => ({ entries: trayEntries() }),
    send: async (arg) => doSend(arg.id, arg),
    notWork: async (id) => {
      const it = find(id);
      it.category = "personal";
      save();
      return { item: clean(it) };
    },
    unsend: async (id) => {
      const entry = st.outbox.find((o) => o.item === id && !o.delivered);
      const row = st.sent.find((s) => s.item === id && !s.unsent);
      if (!entry || !row)
        return notOnMobile(
          "Taking back an item that already reached the server",
          "use `tt sent unsend` on the desktop",
        );
      st.outbox = st.outbox.filter((o) => o !== entry);
      row.unsent = true;
      row.can_unsend = false;
      find(id).sent = false;
      save();
      return { how: "discarded" as const };
    },
    rememberShare: async (category, mode) => {
      if (category === "development") st.privacy.development = mode !== "never";
      else if (category === "work" || category === "meeting") {
        if (mode === "auto")
          notOnMobile("Sending automatically", "the phone always asks first");
        st.privacy[category] = mode === "never" ? "never" : "ask";
      }
      save();
      return privacyInfo();
    },
    eod: async (date) => {
      const day = date ?? today();
      const items = st.items.filter((i) => i.date === day);
      const t = totals(items);
      const e: EndOfDay = {
        date: day,
        hours_by_category: Object.fromEntries(
          CATEGORIES.map((c) => [c, tenth(t[c].done)]),
        ) as Record<Category, number>,
        planned_by_category: Object.fromEntries(
          CATEGORIES.map((c) => [c, tenth(t[c].planned)]),
        ) as Record<Category, number>,
        done_count: items.filter((i) => i.status === "done").length,
        open_count: items.filter(
          (i) =>
            (i.status === "planned" || i.status === "active") &&
            i.kind !== "meeting",
        ).length,
        unsent: trayEntries().filter(
          (e2) =>
            (e2.start ?? "").startsWith(day) ||
            (e2.due_at ?? "").startsWith(day),
        ),
        sent_count: items.filter((i) => i.sent).length,
      };
      return e;
    },
    eodSendAll: async () => {
      const sent = [];
      const needs: string[] = [];
      for (const e of trayEntries()) {
        if (e.needs_project) needs.push(e.id);
        else
          sent.push(
            doSend(e.id, { project: e.suggestion.project ?? undefined }),
          );
      }
      return { sent, needs_project: needs };
    },
    eodConfirm: async () =>
      notOnMobile(
        "Confirming the day's log",
        "confirm it from the web console or the desktop app",
      ),
    weeklyReview: async (date) => {
      const start = mondayOf(date ?? today());
      const items = st.items.filter(
        (i) => i.date >= start && i.date <= addDays(start, 6),
      );
      const t = totals(items);
      const sum = (f: (v: Totals[Category]) => number) =>
        CATEGORIES.reduce((a, c) => a + f(t[c]), 0);
      const plannedMin = sum((v) => v.planned);
      const doneMinutes = sum((v) => v.done);
      const capacity = st.settings.capacityHoursPerWeek * 60;
      const w: WeeklyReview = {
        week_start: start,
        planned_h: tenth(plannedMin),
        done_h: tenth(doneMinutes),
        completion_pct: plannedMin
          ? Math.round((100 * doneMinutes) / plannedMin)
          : 0,
        development_h: tenth(t.development.done),
        meetings_h: tenth(t.meeting.done),
        meeting_load_pct: capacity
          ? Math.round((100 * t.meeting.done) / capacity)
          : 0,
        streaks: [],
        development_items: items
          .filter((i) => i.category === "development" && doneMin(i) > 0)
          .map((i) => ({ title: i.title, hours: tenth(doneMin(i)) })),
      };
      return w;
    },
    developmentSummary: async () =>
      notOnMobile("The weekly development note", "write it on the desktop"),
    deliver: async () => {
      const queue = st.outbox.filter((o) => !o.delivered);
      if (!queue.length) return { sent: 0, offline: false, kept: 0, failed: 0 };
      const base = (
        st.settings.server ||
        globalThis.location?.origin ||
        ""
      ).replace(/\/$/, "");
      let sent = 0;
      let failed = 0;
      let offline = false;
      for (const o of queue) {
        try {
          const r = await doFetch(
            `${base}/api/v1/w/${encodeURIComponent(st.settings.workspace)}/capture/batches`,
            {
              method: "POST",
              headers: {
                "content-type": "application/json",
                ...(st.settings.token
                  ? { authorization: `Bearer ${st.settings.token}` }
                  : {}),
              },
              body: JSON.stringify(o.batch),
            },
          );
          if (r.ok) {
            o.delivered = true;
            o.error = undefined;
            sent += 1;
          } else if (r.status >= 500 || r.status === 429) {
            offline = true; // the server is struggling: keep it and retry later
            break;
          } else {
            o.error = `the server answered ${r.status}`;
            failed += 1;
          }
        } catch {
          offline = true; // no network: keep everything for later
          break;
        }
      }
      st.outbox = st.outbox.filter(
        (o) =>
          !o.delivered ||
          st.sent.some((s) => s.client_id === o.client_id && !s.unsent),
      );
      save();
      return {
        sent,
        offline,
        kept: st.outbox.filter((o) => !o.delivered).length,
        failed,
      };
    },
    privacy: async () => privacyInfo(),
    firstRun: async ({ work, meeting, development }) => {
      st.privacy = {
        work: work === "never" ? "never" : "ask",
        meeting: meeting === "never" ? "never" : "ask",
        development,
        first_run_done: true,
      };
      save();
      return privacyInfo();
    },
    sent: async () => ({
      sent: st.sent.map((s) => ({
        ...s,
        server_key: s.server_key,
        can_unsend:
          !s.unsent &&
          st.outbox.some((o) => o.client_id === s.client_id && !o.delivered),
      })),
    }),
    eraseLocal: async () => {
      st = blank();
      try {
        storage?.removeItem(STORAGE_KEY);
      } catch {
        /* nothing to remove */
      }
      return {
        files: ["browser storage (tt.mobile.v1)"],
        secrets: ["server token"],
      };
    },
    calSettings: async (): Promise<CalendarSettings> => ({
      calendars: [],
      refreshed_at: { google: null, microsoft: null },
      refresh_hours: 0,
      suppress: { attendees: [], domains: [] },
    }),
    calExclude: async () =>
      notOnMobile("Calendars", "connect and manage them on the desktop"),
    calSuppress: async () =>
      notOnMobile("Calendars", "connect and manage them on the desktop"),
    calRefresh: async () =>
      notOnMobile("Calendars", "connect and manage them on the desktop"),
    calSync: async () =>
      notOnMobile("Calendars", "connect and manage them on the desktop"),
    notifications: async () => {
      const fresh = trayEntries().filter((e) => !st.notified.includes(e.id));
      if (fresh.length) {
        st.notified = [...st.notified, ...fresh.map((e) => e.id)].slice(-500);
        save();
      }
      return { entries: fresh };
    },
  };
  return bridge;
}
