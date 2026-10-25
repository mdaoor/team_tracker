import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { Row } from "../api/http";

export const LAYOUTS = ["list", "board", "pipeline", "matrix", "gantt", "calendar", "now"] as const;
export type Layout = (typeof LAYOUTS)[number];

/** A filter chip is a clause (or the negation of one) in the SavedView filter grammar. */
export type Chip = { id: string; label: string; clause: Row; negate?: boolean };
export type Crumb = { label: string; chips: Chip[] };

export interface ViewState {
  chips: Chip[];
  layout: Layout;
  groupBy: string | null;
  sort: string;
  scheme: string | null;
  matrix: { rows: string; cols: string; measure: string };
  drill: Crumb[];
  /** `?f=` / `?d=` was present but could not be decoded: the page ignores it and says so. */
  ignored: boolean;
}

const b64 = {
  enc: (v: unknown) => btoa(unescape(encodeURIComponent(JSON.stringify(v)))),
  dec: <T>(s: string | null, fallback: T): T => {
    if (!s) return fallback;
    try {
      return JSON.parse(decodeURIComponent(escape(atob(s)))) as T;
    } catch {
      return fallback;
    }
  },
};

/** Merge chips into one grammar filter: same-key clauses are merged where that is meaningful, the rest are AND-ed. */
export function toFilter(chips: Chip[]): Row {
  const out: Row = {};
  const and: Row[] = [];
  for (const c of chips) {
    if (c.negate) {
      and.push({ not: c.clause });
      continue;
    }
    for (const [k, v] of Object.entries(c.clause)) {
      if (k === "dims" && out.dims) out.dims = { ...out.dims, ...v };
      else if (k in out) and.push({ [k]: v });
      else out[k] = v;
    }
  }
  if (and.length) out.and = and;
  return out;
}

/** True when a param is present but is not valid base64-JSON. */
export function undecodable(raw: string | null): boolean {
  if (!raw) return false;
  try {
    JSON.parse(decodeURIComponent(escape(atob(raw))));
    return false;
  } catch {
    return true;
  }
}

export function useViewState(): [ViewState, (patch: Partial<ViewState>) => void] {
  const [sp, setSp] = useSearchParams();
  const state = useMemo<ViewState>(
    () => ({
      chips: b64.dec<Chip[]>(sp.get("f"), []),
      layout: (LAYOUTS as readonly string[]).includes(sp.get("layout") ?? "")
        ? (sp.get("layout") as Layout)
        : "list",
      groupBy: sp.get("g"),
      sort: sp.get("s") ?? "priority:desc",
      scheme: sp.get("ws"),
      matrix: b64.dec(sp.get("m"), { rows: "team", cols: "stage", measure: "count" }),
      drill: b64.dec<Crumb[]>(sp.get("d"), []),
      ignored: undecodable(sp.get("f")) || undecodable(sp.get("d")),
    }),
    [sp],
  );
  const set = useCallback(
    (patch: Partial<ViewState>) => {
      const next = new URLSearchParams(sp);
      const put = (k: string, v: string | null) => (v ? next.set(k, v) : next.delete(k));
      if ("chips" in patch) put("f", patch.chips!.length ? b64.enc(patch.chips) : null);
      if ("layout" in patch) put("layout", patch.layout === "list" ? null : patch.layout!);
      if ("groupBy" in patch) put("g", patch.groupBy ?? null);
      if ("sort" in patch) put("s", patch.sort === "priority:desc" ? null : patch.sort!);
      if ("scheme" in patch) put("ws", patch.scheme ?? null);
      if ("matrix" in patch) put("m", b64.enc(patch.matrix));
      if ("drill" in patch) put("d", patch.drill!.length ? b64.enc(patch.drill) : null);
      setSp(next);
    },
    [sp, setSp],
  );
  return [state, set];
}

/** Drill-down (UX §4.3): each click appends chips and a breadcrumb; Backspace pops one level so the user can fork. */
export function useDrillPath(state: ViewState, set: (p: Partial<ViewState>) => void) {
  const push = (label: string, chips: Chip[]) =>
    set({ chips: [...state.chips, ...chips], drill: [...state.drill, { label, chips }] });
  const pop = () => {
    const last = state.drill.at(-1);
    if (!last) return;
    const ids = new Set(last.chips.map((c) => c.id));
    set({ chips: state.chips.filter((c) => !ids.has(c.id)), drill: state.drill.slice(0, -1) });
  };
  return { push, pop };
}

export const sortParam = (s: string): { field: string; dir: string }[] => {
  const [field, dir] = s.split(":");
  return [{ field, dir: dir ?? "desc" }];
};

let n = 0;
export const chipId = () => `c${Date.now().toString(36)}${n++}`;

/** Preset chips for the Views landing (UX review, persona metric: "what ships next week?"). */
export const PRESETS: { id: string; label: string; layout: Layout; chip: () => Chip }[] = [
  {
    id: "next-week",
    label: "What's shipping next week",
    layout: "list",
    chip: () => ({
      id: chipId(),
      label: "due next week",
      clause: {
        or: [
          { planned_end: { between: ["start_of_next_week", "end_of_next_week"] } },
          { due_date: { between: ["start_of_next_week", "end_of_next_week"] } },
        ],
      },
    }),
  },
  {
    id: "this-week",
    label: "Due this week",
    layout: "calendar",
    chip: () => ({
      id: chipId(),
      label: "due this week",
      clause: {
        or: [
          { planned_end: { between: ["start_of_week", "end_of_week"] } },
          { due_date: { between: ["start_of_week", "end_of_week"] } },
        ],
      },
    }),
  },
  {
    id: "overdue",
    label: "Overdue",
    layout: "list",
    chip: () => ({
      id: chipId(),
      label: "overdue",
      clause: { due_date: { before: "today" }, state: { category: ["open", "active"] } },
    }),
  },
];
