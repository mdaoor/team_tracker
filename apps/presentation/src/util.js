export const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
export const PRIO_RANK = {
  low: 0,
  medium: 1,
  high: 2,
  highest: 3,
  critical: 4,
};

export function teamColor(D, t) {
  const i = Math.max(0, D.teams.indexOf(t));
  return `var(--s${(i % 4) + 1})`;
}

/** Declarative drill filter (JSON in `data-drill`): the same spec drives the deck overlay and page-mode breadcrumbs. */
export function applyFilter(items, f, D) {
  return items.filter((i) => {
    if (f.state && i.state !== f.state) return false;
    if (f.stage && i.stage !== f.stage) return false;
    if (f.project && i.project !== f.project) return false;
    if (f.assignee && i.assignee !== f.assignee) return false;
    if (f.hours && !(i.hours_logged > 0)) return false;
    if (f.late && !(i.actual_end && i.actual_end > i.planned_end)) return false;
    if (f.risk && !i.risks.length) return false;
    if (f.violated && !violationsOf(D, [i]).length) return false;
    if (f.overdue && !(i.state !== "done" && i.planned_end < f.overdue))
      return false;
    if (f.dims)
      for (const [d, v] of Object.entries(f.dims))
        if (!keysOf(i, d).includes(v)) return false;
    return true;
  });
}
export const drillAttr = (label, f) =>
  `data-drill="${esc(JSON.stringify({ label, f }))}"`;

export function keysOf(i, dim) {
  return dim === "tags" ? (i.tags.length ? i.tags : ["—"]) : [i[dim] ?? "—"];
}

export function scopeItems(D, scope) {
  const [kind, id] = scope.split(":");
  if (kind === "product") return D.items.filter((i) => i.product === id);
  if (kind === "project") return D.items.filter((i) => i.project === id);
  if (kind === "team") return D.items.filter((i) => i.team === id);
  if (kind === "person") return D.items.filter((i) => i.assignee === id);
  return D.items;
}

export function violationsOf(D, items) {
  const keys = new Set(items.map((i) => i.key));
  return D.violations.filter(
    (v) =>
      keys.has(v.target) ||
      (v.target.includes("/") && items.some((i) => v.project === i.project)),
  );
}

export function measureOf(D, list, m) {
  if (m === "count") return list.length;
  if (m === "hours")
    return +list.reduce((s, i) => s + i.hours_logged, 0).toFixed(0);
  if (m === "wt")
    return list
      .filter((i) => i.state === "done")
      .reduce((s, i) => s + i.weight, 0);
  if (m === "viol") return violationsOf(D, list).length;
  return 0;
}

export function sortKeys(D, vals, dim) {
  const a = [...vals];
  if (dim === "stage")
    return a.sort((x, y) => D.stages.indexOf(x) - D.stages.indexOf(y));
  if (dim === "priority")
    return a.sort((x, y) => (PRIO_RANK[x] ?? 9) - (PRIO_RANK[y] ?? 9));
  return a.sort();
}

export const sev = (s) =>
  ({ info: "", warning: "warn", violation: "serious", critical: "crit" })[s] ??
  "";
export const stuck = (D, i) =>
  i.state !== "done" &&
  i.stage_age_days > (D.stage_expected_days[i.stage] ?? Infinity);

/** A project is shown when it has reported work or a plan (a plan-only project has deliverables but no items yet). */
export const projectShown = (ctx, p) =>
  ctx.items.some((i) => i.project === p.key) ||
  (ctx.D.deliverables ?? []).some((d) => d.project === p.key) ||
  (ctx.D.phases ?? []).some((ph) => ph.project === p.key);

