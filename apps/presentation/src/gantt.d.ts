/** Types for `@tt/presentation/gantt`: the one Gantt implementation (deck Roadmap slide and, once wired, the web console). */

export type Zoom = "day" | "week" | "month" | "fit";
export const ZOOMS: readonly Zoom[];

export interface GanttRowIn {
  key: string;
  title?: string;
  planned_start?: string | null;
  planned_end?: string | null;
  /** row kind, used as a CSS class and label style: phase | deliverable | epic | milestone | item */
  type?: string;
  /** not_started | in_progress | done | completed | overdue | at_risk | due_soon | planned */
  status?: string | null;
  state_category?: string | null;
  progress_pct?: number | null;
  rollup?: boolean;
  depth?: number;
  baseline?: {
    planned_start?: string | null;
    planned_end?: string | null;
  } | null;
}
export interface GanttDepIn {
  from: string;
  to: string;
}
export interface GanttMilestoneIn {
  name: string;
  due: string;
  project?: string;
  status?: string;
}

export interface GanttBar {
  key: string;
  title: string;
  type: string;
  status: string | null;
  depth: number;
  x: number;
  w: number;
  y: number;
  dated: boolean;
  done: boolean;
  rollup: boolean;
  progress: number | null;
  ghost?: { x: number; w: number };
  slip?: boolean;
}
export interface GanttModel {
  width: number;
  height: number;
  labelWidth: number;
  rowHeight: number;
  axisHeight: number;
  zoom: Zoom;
  /** true for day/week/month: the SVG has a fixed pixel size and wants a horizontally scrolling wrapper */
  scroll: boolean;
  bars: GanttBar[];
  ticks: { x: number; label: string; major: boolean }[];
  todayX: number | null;
  milestones: {
    x: number;
    name: string;
    due: string;
    project: string;
    status: string;
  }[];
  arrows: { d: string; from: string; to: string }[];
}

export function layoutGantt(o: {
  rows: GanttRowIn[];
  deps?: GanttDepIn[];
  milestones?: GanttMilestoneIn[];
  today?: string | null;
  zoom?: Zoom;
  width?: number;
  labelWidth?: number;
  rowHeight?: number;
  axisHeight?: number;
}): GanttModel;
export function ganttSvg(model: GanttModel, opts?: { label?: string }): string;
export function ganttTable(model: GanttModel, rows: GanttRowIn[]): string;
export function statusName(status: string): string;
export function snapshotRows(
  D: unknown,
  items: unknown[],
  opts?: { rows?: "phase" | "epic" | "milestone"; project_key?: string | null },
): { rows: GanttRowIn[]; deps: GanttDepIn[] };
