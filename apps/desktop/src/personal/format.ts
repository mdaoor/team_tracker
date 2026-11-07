import type { Category, ItemRow } from "./types";

/** 90 -> "1 h 30", 120 -> "2 h", 20 -> "20 min", 0/null -> "-". */
export function hm(minutes: number | null | undefined): string {
  if (!minutes) return "-";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h && !m) return `${h} h`;
  if (h) return `${h} h ${String(m).padStart(2, "0")}`;
  return `${m} min`;
}

/** "2026-10-23T09:00:00+03:00" -> "09:00": the wall clock the API already put in the person's zone. */
export function clock(iso: string | null | undefined): string {
  return iso ? iso.slice(11, 16) : "";
}

export function range(row: Pick<ItemRow, "start" | "end" | "all_day">): string {
  if (row.all_day || !row.start) return "Anytime";
  return row.end ? `${clock(row.start)}–${clock(row.end)}` : clock(row.start);
}

const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** Parse "YYYY-MM-DD" without time-zone surprises (the zone of the machine must not shift the day). */
export function ymd(iso: string): {
  y: number;
  m: number;
  d: number;
  wd: number;
} {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return { y, m, d, wd: new Date(Date.UTC(y, m - 1, d)).getUTCDay() };
}

export function dayLabel(iso: string): string {
  const { m, d, wd } = ymd(iso);
  return `${DAY[wd]} ${d} ${MONTH[m - 1].slice(0, 3)}`;
}
export function dayLong(iso: string): string {
  const { y, m, d, wd } = ymd(iso);
  return `${DAY[wd]} ${d} ${MONTH[m - 1]} ${y}`;
}
export function monthLabel(year: number, month: number): string {
  return `${MONTH[month - 1]} ${year}`;
}

export function addDays(iso: string, n: number): string {
  const { y, m, d } = ymd(iso);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}

export const CATEGORY_LABEL: Record<Category, string> = {
  work: "Work",
  meeting: "Meeting",
  development: "Development",
  personal: "Personal",
};
/** Shape + colour: each category has its own glyph so colour is never the only cue. */
export const CATEGORY_GLYPH: Record<Category, string> = {
  work: "■",
  meeting: "◆",
  development: "▲",
  personal: "●",
};

export function statusLabel(row: ItemRow): string {
  if (row.status === "done") return "done";
  if (row.status === "skipped") return "skipped";
  if (row.timer === "running") return "timer running";
  return row.sent ? "sent" : "";
}
