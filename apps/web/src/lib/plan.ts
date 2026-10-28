import type { PlanRow } from "../api/types";

export const STATUS_LABEL: Record<string, string> = {
  done: "Done",
  overdue: "Overdue",
  at_risk: "At risk",
  in_progress: "In progress",
  not_started: "Not started",
  completed: "Completed",
  due_soon: "Due soon",
  planned: "Planned",
};
export const HEALTH_LABEL: Record<string, string> = {
  green: "On track",
  amber: "Needs attention",
  red: "Off track",
  unknown: "No plan yet",
};

/** Axis for the roadmap bars: the plan's own min/max dates, padded a little. */
export function axisOf(rows: { planned_start?: string | null; planned_end?: string | null }[]): {
  from: number;
  to: number;
} | null {
  const ts = rows.flatMap((r) => [r.planned_start, r.planned_end]).filter(Boolean) as string[];
  if (!ts.length) return null;
  const ms = ts.map((t) => Date.parse(t));
  const from = Math.min(...ms);
  const to = Math.max(...ms);
  const pad = Math.max(86_400_000, (to - from) * 0.02);
  return { from: from - pad, to: to + pad };
}

/** Bar position in percent of the axis; a deliverable with only an end date shows as a point-sized bar. */
export function barOf(
  r: { planned_start?: string | null; planned_end?: string | null },
  axis: { from: number; to: number },
): { left: number; width: number } | null {
  const end = r.planned_end ? Date.parse(r.planned_end) : null;
  const start = r.planned_start ? Date.parse(r.planned_start) : end;
  if (start == null || end == null) return null;
  const span = axis.to - axis.from || 1;
  const left = ((Math.min(start, end) - axis.from) / span) * 100;
  const width = Math.max(1, ((Math.abs(end - start) || 86_400_000) / span) * 100);
  return { left: Math.max(0, left), width: Math.min(100 - Math.max(0, left), width) };
}

/** A date edit is valid when it is a real date and does not put the end before the start. */
export function dateEditError(start: string | null, end: string | null): string | null {
  for (const d of [start, end]) if (d && Number.isNaN(Date.parse(d))) return "not a date";
  if (start && end && end < start) return "the end is before the start";
  return null;
}

export function deliverablesOf(plan: { phases?: { deliverables?: PlanRow[] }[] }): PlanRow[] {
  return (plan.phases ?? []).flatMap((p) => p.deliverables ?? []);
}
