import type { Row } from "../api/http";

export type Zoom = "day" | "week" | "month";
const DAY = 86_400_000;
const PX: Record<Zoom, number> = { day: 28, week: 8, month: 2.6 };
export const LABEL_W = 170;
export const ROW_H = 26;
export const AXIS_H = 34;

const ms = (iso: string) => Date.parse(iso + "T00:00:00Z");
const iso = (t: number) => new Date(t).toISOString().slice(0, 10);

export type Bar = { key: string; title: string; x: number; w: number; y: number; done: boolean; rollup: boolean; ghost?: { x: number; w: number }; slip?: boolean; type: string };
export type Tick = { x: number; label: string; major: boolean };
export type Arrow = { d: string; from: string; to: string };

export interface GanttModel {
  width: number;
  height: number;
  bars: Bar[];
  ticks: Tick[];
  todayX: number | null;
  milestones: { x: number; name: string; project: string }[];
  arrows: Arrow[];
}

/**
 * Pure layout for the Gantt (UX review 11): a time axis with day/week/month ticks, a today line, bar labels, milestone
 * diamonds and elbow arrows for dependencies. All coordinates are pixels, so the SVG component only draws.
 */
export function layoutGantt(
  rows: Row[],
  deps: Row[],
  milestones: Row[],
  today: string | null,
  zoom: Zoom,
): GanttModel {
  const scheduled = rows.filter((r) => r.planned_start && r.planned_end);
  const points = [
    ...scheduled.flatMap((r) => [ms(r.planned_start), ms(r.planned_end), ...(r.baseline ? [ms(r.baseline.planned_start), ms(r.baseline.planned_end)] : [])]),
    ...milestones.filter((m) => m.due).map((m) => ms(m.due)),
    ...(today ? [ms(today)] : []),
  ];
  if (!points.length) return { width: LABEL_W, height: AXIS_H, bars: [], ticks: [], todayX: null, milestones: [], arrows: [] };
  const lo = Math.min(...points) - 3 * DAY;
  const hi = Math.max(...points) + 7 * DAY;
  const px = PX[zoom];
  const X = (t: number) => LABEL_W + ((t - lo) / DAY) * px;
  const bars: Bar[] = scheduled.map((r, i) => ({
    key: r.key,
    title: r.title ?? r.key,
    type: r.type ?? "item",
    x: X(ms(r.planned_start)),
    w: Math.max(3, X(ms(r.planned_end) + DAY) - X(ms(r.planned_start))),
    y: AXIS_H + i * ROW_H,
    done: r.state_category === "done",
    rollup: !!r.rollup,
    slip: !!r.baseline && ms(r.planned_end) > ms(r.baseline.planned_end),
    ghost: r.baseline
      ? { x: X(ms(r.baseline.planned_start)), w: Math.max(2, X(ms(r.baseline.planned_end) + DAY) - X(ms(r.baseline.planned_start))) }
      : undefined,
  }));
  const ticks: Tick[] = [];
  for (let t = lo - (((new Date(lo).getUTCDay() + 6) % 7) * DAY); t <= hi; t += DAY) {
    const d = new Date(t);
    if (zoom === "day") ticks.push({ x: X(t), label: String(d.getUTCDate()), major: d.getUTCDate() === 1 || d.getUTCDay() === 1 });
    else if (zoom === "week" && d.getUTCDay() === 1) ticks.push({ x: X(t), label: iso(t).slice(5), major: d.getUTCDate() <= 7 });
    else if (zoom === "month" && d.getUTCDate() === 1)
      ticks.push({ x: X(t), label: d.toLocaleString("en", { month: "short", year: "2-digit", timeZone: "UTC" }), major: d.getUTCMonth() === 0 });
  }
  const byKey = new Map(bars.map((b) => [b.key, b]));
  const arrows: Arrow[] = [];
  for (const d of deps) {
    const a = byKey.get(d.from);
    const b = byKey.get(d.to);
    if (!a || !b) continue;
    const x1 = a.x + a.w;
    const y1 = a.y + ROW_H / 2;
    const x2 = b.x;
    const y2 = b.y + ROW_H / 2;
    const mid = Math.max(x1 + 6, x2 - 6);
    arrows.push({ from: d.from, to: d.to, d: `M${x1},${y1} H${mid} V${y2} H${x2}` });
  }
  return {
    width: X(hi) + 20,
    height: AXIS_H + scheduled.length * ROW_H + 10,
    bars,
    ticks,
    todayX: today ? X(ms(today)) : null,
    milestones: milestones.filter((m) => m.due).map((m) => ({ x: X(ms(m.due)), name: m.name, project: m.project })),
    arrows,
  };
}
