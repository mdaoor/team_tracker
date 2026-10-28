/** Wire types for the personal tracker views (Phase 15). They mirror `tt_client.personal.bridge` 1:1; `contract.json` (written by the
 * Python test-suite) is the machine-checked source of truth and `contract.test.ts` fails when these drift. The views never parse,
 * store or send: they call the `PersonalBridge`, which is the Python sidecar in the shells (ADR-0038) and an in-memory double in tests. */

export type Category = "work" | "meeting" | "development" | "personal";
export type Status = "planned" | "active" | "done" | "skipped" | "cancelled";
export type SharePolicy = "never" | "ask" | "auto";
export const CATEGORIES: Category[] = [
  "work",
  "meeting",
  "development",
  "personal",
];

export interface ItemRow {
  id: string;
  title: string;
  kind: string;
  category: Category;
  status: Status;
  start: string | null;
  end: string | null;
  all_day: boolean;
  duration_min: number | null;
  recurring: boolean;
  series_id: string | null;
  tags: string[];
  project_hint: string | null;
  share_policy: SharePolicy;
  sent: boolean;
  sendable: boolean;
  online_meeting_url: string | null;
  location: string | null;
  attendees: string[];
  from_calendar: "google" | "microsoft" | null;
  minutes_planned: number;
  minutes_done: number;
  timer: "running" | "paused" | "stopped" | null;
  elapsed_min: number;
}

export interface ServerItem {
  key: string;
  title: string;
  project: string;
  server: boolean;
}
export type Totals = Record<Category, { planned: number; done: number }>;

export interface DayView {
  date: string;
  tz: string;
  timeline: ItemRow[];
  anytime: ItemRow[];
  server_items: ServerItem[];
  totals: Totals;
  planned_min: number;
  capacity_min: number;
  load_pct: number;
  timer: { item_id: string; elapsed_min: number } | null;
}
export interface WeekView {
  week_start: string;
  days: DayView[];
  totals: Totals;
  planned_min: number;
  capacity_min: number;
}
export interface MonthDay {
  dots: Category[];
  minutes: Partial<Record<Category, number>>;
  sent_min: number;
  private_min: number;
}
export interface MonthView {
  year: number;
  month: number;
  weeks: string[][];
  days: Record<string, MonthDay>;
  totals_h: Record<Category, number>;
  sparkline: { date: string; sent: number; private: number }[];
}

export interface Suggestion {
  project: string | null;
  category: Category;
  share_policy: SharePolicy;
  confidence: number;
  cue: "✓" | "suggested" | "ask";
  source: string | null;
  reasons: string[];
  alternatives: string[];
  rule_id: string | null;
}
export interface TrayEntry {
  id: string;
  title: string;
  kind: string;
  category: Category;
  start: string | null;
  end: string | null;
  minutes: number;
  note: string;
  due_at: string;
  auto: boolean;
  /** The exact note an automatic send of this entry carries now (neutral until the series note template is confirmed). */
  auto_sends: string;
  online_meeting_url: string | null;
  suggestion: Suggestion;
  needs_project: boolean;
  label: string;
}
export interface Draft {
  title: string;
  kind: string;
  category: Category;
  start: string | null;
  end: string | null;
  duration_min: number | null;
  all_day: boolean;
  rrule: string | null;
  rrule_text: string | null;
  tags: string[];
  project_hint: string | null;
  warnings: string[];
  notes: string;
  kind_source: "label" | "keyword" | "default";
  category_source: "tag" | "kind";
  suggested_kind: string | null; // a model's less private reading: shown as a question, never applied
  suggested_category: Category | null;
}
export interface SentRow {
  item: string;
  title: string;
  client_id: string;
  server_key: string | null;
  project: string | null;
  minutes: number | null;
  at: string;
  unsent: boolean;
  can_unsend: boolean;
}
export interface PrivacyInfo {
  stays_on_device: string;
  store: string;
  encrypted: boolean;
  key_store: string;
  categories: {
    work: "ask" | "auto" | "never";
    meeting: "ask" | "auto" | "never";
    development: "on" | "off";
    personal: "never";
  };
  assist: "off" | "on";
  assist_note: string;
  first_run_done: boolean;
  calendars: string[];
}
export interface EndOfDay {
  date: string;
  hours_by_category: Record<Category, number>;
  planned_by_category: Record<Category, number>;
  done_count: number;
  open_count: number;
  unsent: TrayEntry[];
  sent_count: number;
}
export interface WeeklyReview {
  week_start: string;
  planned_h: number;
  done_h: number;
  completion_pct: number;
  development_h: number;
  meetings_h: number;
  meeting_load_pct: number;
  streaks: {
    series_id: string;
    title: string;
    streak: number;
    done: number;
    of: number;
  }[];
  development_items: { title: string; hours: number }[];
}
export interface SendResult {
  item: string;
  client_id: string;
  project: string | null;
  minutes: number | null;
  tags: string[];
}
export interface TimerStatus {
  item: string;
  title: string | null;
  elapsed_min: number;
  state: string;
}

export class BridgeError extends Error {
  constructor(
    public code: string,
    message: string,
    public alternatives: string[] = [],
  ) {
    super(message);
  }
}

/** Everything the personal views may do; one method per sidecar command. */
export interface PersonalBridge {
  day(date?: string): Promise<DayView>;
  week(date?: string): Promise<WeekView>;
  month(year?: number, month?: number): Promise<MonthView>;
  preview(line: string): Promise<{ drafts: Draft[] }>;
  add(line: string): Promise<{ items: ItemRow[] }>;
  recurAdd(
    text: string,
    sendEach?: boolean,
  ): Promise<{
    id: string;
    title: string;
    rule: string;
    rrule: string;
    category: Category;
  }>;
  complete(id: string, minutes?: number): Promise<{ item: ItemRow }>;
  skip(id: string): Promise<{ item: ItemRow }>;
  reopen(id: string): Promise<{ item: ItemRow }>;
  remove(id: string): Promise<{ deleted: boolean }>;
  reschedule(id: string, start: string): Promise<{ item: ItemRow }>;
  setCategory(id: string, category: Category): Promise<{ item: ItemRow }>;
  timerStart(arg: {
    id?: string;
    title?: string;
  }): Promise<{ item: string; title: string; state: string; paused?: string }>;
  timerStop(): Promise<{
    stopped: { item: string; title: string | null; minutes: number } | null;
  }>;
  timerStatus(): Promise<{ timer: TimerStatus | null }>;
  tray(): Promise<{ entries: TrayEntry[] }>;
  send(arg: {
    id: string;
    project?: string;
    note?: string;
    minutes?: number;
  }): Promise<SendResult>;
  notWork(id: string): Promise<{ item: ItemRow }>;
  unsend(id: string): Promise<{ how: "discarded" | "correction" }>;
  rememberShare(category: Category, mode: string): Promise<PrivacyInfo>;
  eod(date?: string): Promise<EndOfDay>;
  eodSendAll(
    date?: string,
  ): Promise<{ sent: SendResult[]; needs_project: string[] }>;
  eodConfirm(date?: string): Promise<{
    confirmed: boolean;
    reason?: string;
    minutes_total?: number;
    /** One plain sentence, e.g. "Confirmed your day: 45 min on WEB-114." */
    sentence?: string;
  }>;
  weeklyReview(date?: string): Promise<WeeklyReview>;
  developmentSummary(
    date?: string,
  ): Promise<{ client_id: string; note: string }>;
  deliver(): Promise<{
    sent: number;
    offline: boolean;
    kept: number;
    failed: number;
  }>;
  privacy(): Promise<PrivacyInfo>;
  firstRun(arg: {
    work: string;
    meeting: string;
    development: boolean;
    assist: boolean;
  }): Promise<PrivacyInfo>;
  sent(): Promise<{ sent: SentRow[] }>;
  eraseLocal(): Promise<{ files: string[]; secrets: string[] }>;
  /** Calendar preferences (no network): the cached calendar lists with their switches, and the never-offer lists. */
  calSettings(): Promise<CalendarSettings>;
  /** `excluded: true` stops reading that calendar (id, name or part of either); false reads it again. */
  calExclude(calendar: string, excluded: boolean): Promise<CalendarSettings>;
  calSuppress(
    kind: "attendee" | "domain",
    value: string,
    remove?: boolean,
  ): Promise<CalendarSettings>;
  /** Re-list the calendars at each connected provider. */
  calRefresh(provider?: string): Promise<
    CalendarSettings & {
      refreshed: {
        provider: string;
        added: string[];
        removed: string[];
        error: string | null;
      }[];
    }
  >;
  /** One calendar sync now (the shell also calls it on a timer). */
  calSync(full?: boolean): Promise<{ reports: CalendarSyncReport[] }>;
  /** Ready-to-send entries the shell has not yet told the person about; each is returned once. */
  notifications(): Promise<{ entries: TrayNotification[] }>;
}
export interface CalendarRow {
  provider: string;
  id: string;
  name: string;
  primary: boolean;
  /** false: the provider itself hides this calendar (for example Google "Holidays" unticked in the calendar list). */
  selected: boolean;
  excluded: boolean;
}
export interface CalendarSettings {
  calendars: CalendarRow[];
  refreshed_at: Record<string, string | null>;
  refresh_hours: number;
  suppress: { attendees: string[]; domains: string[] };
}
export interface CalendarSyncReport {
  provider: string;
  added: number;
  updated: number;
  removed: number;
  skipped: Record<string, number>;
  calendars: number;
  error: string | null;
  retry_after: number | null;
}
/** What a system notification needs of a tray entry ("Sprint planning ended: send 1 h to WEB?"). */
export type TrayNotification = Pick<
  TrayEntry,
  "id" | "title" | "minutes" | "label" | "suggestion"
>;
