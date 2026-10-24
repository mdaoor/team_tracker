import type { Row } from "../api/http";

/** A template parameter (`int | number | string | ident`) becomes an input spec the form renders (Phase 9 §form generation). */
export type Control = {
  name: string;
  kind: "number" | "text" | "select";
  min?: number;
  max?: number;
  step?: number;
  help: string;
  placeholder?: string;
  options?: string[];
};

export function controlFor(p: Row, stages: string[] = []): Control {
  const base = { name: p.name as string, help: (p.help as string) ?? "" };
  const lname = String(p.name);
  if (p.type === "int" || p.type === "number") {
    return {
      ...base,
      kind: "number",
      min: p.min ?? undefined,
      max: p.max ?? undefined,
      step: p.type === "int" ? 1 : 0.1,
      placeholder: p.default == null ? "default" : String(p.default),
    };
  }
  if (p.type === "ident" && (lname === "stage" || lname.endsWith("_stage")) && stages.length) {
    return { ...base, kind: "select", options: stages };
  }
  return { ...base, kind: "text", placeholder: p.default == null ? "" : String(p.default) };
}

/** Form values → API params: empty fields are left out (the template default applies); numbers are numbers. */
export function paramsFrom(
  controls: Control[],
  values: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const c of controls) {
    const v = (values[c.name] ?? "").trim();
    if (v === "") continue;
    out[c.name] = c.kind === "number" ? Number(v) : v;
  }
  return out;
}

export type CelProblem = { message: string; field?: string; line?: number; column?: number };

/** Map a server error to an editor range `[from, to)`; unknown positions mark the whole first line. */
export function problemRange(src: string, e: CelProblem): [number, number] {
  const lines = src.split("\n");
  const li = Math.min(Math.max((e.line ?? 1) - 1, 0), lines.length - 1);
  let from = 0;
  for (let i = 0; i < li; i++) from += lines[i].length + 1;
  const col = Math.min(Math.max((e.column ?? 1) - 1, 0), lines[li].length);
  const to = e.column ? Math.min(from + col + 1, from + lines[li].length) : from + lines[li].length;
  return [from + (e.column ? col : 0), Math.max(to, from + (e.column ? col : 0))];
}

export const SEVERITIES = ["critical", "serious", "warning", "info"] as const;
export const sevRank = (s: string): number => SEVERITIES.indexOf(s as (typeof SEVERITIES)[number]);

/** Group by severity, then policy (the inbox default). */
export function groupViolations(
  rows: Row[],
): { severity: string; policies: { policy: string; rows: Row[] }[] }[] {
  const by = new Map<string, Map<string, Row[]>>();
  for (const r of rows) {
    const p = by.get(r.severity) ?? new Map<string, Row[]>();
    p.set(r.policy ?? "?", [...(p.get(r.policy ?? "?") ?? []), r]);
    by.set(r.severity, p);
  }
  return [...by.entries()]
    .sort((a, b) => sevRank(a[0]) - sevRank(b[0]))
    .map(([severity, p]) => ({
      severity,
      policies: [...p.entries()].map(([policy, rows]) => ({ policy, rows })),
    }));
}

/** `Violation.evidence` as "field: value" chips; internal keys and objects are dropped. */
export function evidenceChips(ev: Row | null | undefined): string[] {
  return Object.entries(ev ?? {})
    .filter(
      ([k, v]) => !["policy", "placement_id"].includes(k) && v != null && typeof v !== "object",
    )
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`);
}

/** Trend of how well the person is doing (negative-polarity habits improve when they occur less). */
export const trendGlyph = (t: string | null | undefined): string =>
  t === "improving" ? "↑" : t === "worsening" ? "↓" : "→";

/** CSV for a 1:1, stamped with who exported it and when (the plan's "export for 1:1"). */
export function habitsCsv(rows: Row[], by: string, at = new Date()): string {
  const q = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const head = ["subject", "habit", "polarity", "window", "occurrences", "rate", "trend", "status"];
  return [
    `# Exported by ${by} at ${at.toISOString()}`,
    head.join(","),
    ...rows.map((r) =>
      [r.subject, r.key, r.polarity, r.window, r.occurrences, r.rate, r.trend, r.status]
        .map(q)
        .join(","),
    ),
  ].join("\n");
}

export const HABIT_FLOW = ["observed", "flagged", "in_coaching", "resolved"] as const;

export function ageLabel(iso: string, now = Date.now()): string {
  const h = Math.max(0, (now - Date.parse(iso)) / 3_600_000);
  return h < 1 ? "just now" : h < 48 ? `${Math.floor(h)}h` : `${Math.floor(h / 24)}d`;
}

/** Dry-run gate: activation needs a run after the last edit (admins may override). */
export function canActivate(p: Row, admin: boolean): "yes" | "force" | "no" {
  const ran = p.last_test?.ran_at ? Date.parse(p.last_test.ran_at) : 0;
  if (ran >= Date.parse(p.updated_at)) return "yes";
  return admin ? "force" : "no";
}

export const METRICS: { key: string; label: string; fmt?: (v: number) => string }[] = [
  { key: "throughput_weighted", label: "Throughput (weighted)" },
  { key: "hours_logged", label: "Hours logged" },
  { key: "cycle_time_p50", label: "Cycle time p50, stages summed (days)" },
  { key: "overrun_rate", label: "Overrun rate", fmt: (v) => `${Math.round(v * 100)}%` },
  { key: "estimate_accuracy", label: "Estimate accuracy", fmt: (v) => `${Math.round(v * 100)}%` },
  { key: "wip", label: "WIP" },
  { key: "risk_exposure", label: "Risk exposure" },
  { key: "violations", label: "Violations" },
];

/** One series from snapshot rows; nested `violation_count` collapses to its total. */
export function seriesOf(rows: Row[], key: string): { date: string; v: number | null }[] {
  return rows.map((r) => {
    const raw = key === "violations" ? r.metrics?.violation_count : r.metrics?.[key];
    const v =
      raw == null
        ? null
        : typeof raw === "object"
          ? Object.values(raw as Record<string, number>).reduce((a, b) => a + Number(b), 0)
          : Number(raw);
    return { date: r.date, v };
  });
}

/** Multiplier rows ⇄ the formula's `{ "priority.high": 3 }` map. */
export type MultRow = { key: string; factor: string };
export function formulaFrom(
  base: string,
  constant: string,
  rows: MultRow[],
  clampLo: string,
  clampHi: string,
): Row {
  const multipliers: Record<string, number> = {};
  for (const r of rows)
    if (r.key.trim() && r.factor.trim() !== "" && !Number.isNaN(Number(r.factor)))
      multipliers[r.key.trim()] = Number(r.factor);
  const f: Row = { base, multipliers };
  if (base === "constant") f.constant = Number(constant || 1);
  if (clampLo.trim() !== "" && clampHi.trim() !== "") f.clamp = [Number(clampLo), Number(clampHi)];
  return f;
}
export function rowsFrom(f: Row | null | undefined): MultRow[] {
  return Object.entries(f?.multipliers ?? {}).map(([key, v]) => ({ key, factor: String(v) }));
}
