import { esc } from "../util.js";

/** Plan kinds render from manager-entered data alone (`D.phases`, `D.deliverables`, `D.milestones`, `D.projects[*]` plan fields);
 *  progress, hours and tasks appear when work has been reported. Missing numbers are "—", never zero-width charts. */

const STATUS_LABEL = {
  not_started: "Not started",
  in_progress: "In progress",
  done: "Done",
  overdue: "Overdue",
  at_risk: "At risk",
  completed: "Completed",
  due_soon: "Due soon",
  planned: "Planned",
};
const statusClass = (s) =>
  ({
    overdue: "sev-critical",
    at_risk: "sev-warning",
    due_soon: "sev-warning",
    done: "ok",
    completed: "ok",
  })[s] ?? "";
const chip = (s) =>
  `<span class="chip ${statusClass(s)}">${esc(STATUS_LABEL[s] ?? s)}</span>`;
const bar = (pct) =>
  pct == null
    ? `<span class="muted">—</span>`
    : `<span class="plan-bar" role="img" aria-label="${pct}% done"><i style="width:${pct}%"></i></span> ${pct}%`;
const day = (d) => (d ? esc(d.slice(0, 10)) : "—");
const HEALTH = {
  green: "Healthy",
  amber: "Watch",
  red: "Off track",
  unknown: "No plan",
};

export const planEmpty = (what = "plan") =>
  `<div class="card empty">No ${esc(what)} for this scope yet. Add phases, deliverables and milestones under Setup → Project plan.</div>`;

const planProjects = (D, cfg) => {
  const keys = new Set(
    D.phases.map((p) => p.project).concat(D.milestones.map((m) => m.project)),
  );
  return D.projects.filter((p) =>
    cfg.project_key ? p.key === cfg.project_key : keys.has(p.key),
  );
};

export function renderProjectOverview(ctx, cfg) {
  const { D } = ctx;
  const ps = planProjects(D, cfg);
  if (!ps.length) return planEmpty("project plan");
  return ps
    .map((p) => {
      const ms = D.milestones.filter((m) => m.project === p.key);
      const next = ms.find((m) => m.status !== "completed");
      const ds = D.deliverables.filter((d) => d.project === p.key);
      const done = ds.filter((d) => d.status === "done").length;
      return `<section class="card project-overview" data-project="${esc(p.key)}">
        <h3>${esc(p.name)} <small>${esc(p.key)}</small> ${p.health ? `<span class="chip health-${esc(p.health)}">${esc(HEALTH[p.health] ?? p.health)}</span>` : ""}</h3>
        <dl class="facts">
          <dt>Owner</dt><dd>${esc(p.owner ?? "—")}</dd>
          <dt>Dates</dt><dd>${day(p.planned_start)} → ${day(p.planned_end)}</dd>
          <dt>Status</dt><dd>${p.status ? chip(p.status) : "—"}</dd>
          <dt>Deliverables</dt><dd>${done} of ${ds.length} done</dd>
          <dt>Next milestone</dt><dd>${next ? `${esc(next.name)} · ${day(next.due)} ${chip(next.status)}` : "—"}</dd>
          <dt>Baseline</dt><dd>${esc(p.baseline_name ?? "not approved")}</dd>
        </dl>
        ${p.summary ? `<p>${esc(p.summary)}</p>` : ""}
      </section>`;
    })
    .join("");
}

export function renderMilestones(ctx, cfg) {
  const ms = ctx.D.milestones.filter(
    (m) => !cfg.project_key || m.project === cfg.project_key,
  );
  if (!ms.length) return planEmpty("milestones");
  return `<table class="tbl milestones"><thead><tr><th>Milestone</th><th>Kind</th><th>Due</th><th>Status</th><th>Deliverables</th></tr></thead><tbody>${ms
    .map(
      (m) =>
        `<tr data-status="${esc(m.status)}"><td>${esc(m.name)}${cfg.project_key ? "" : ` <small>${esc(m.project)}</small>`}</td><td>${esc(m.kind)}</td><td>${day(m.due)}</td><td>${chip(m.status)}</td><td>${m.deliverables_total ? `${m.deliverables_done} / ${m.deliverables_total}` : "—"}</td></tr>`,
    )
    .join("")}</tbody></table>`;
}

export function renderDeliverables(ctx, cfg, slide) {
  let ds = ctx.D.deliverables.filter(
    (d) =>
      (!cfg.project_key || d.project === cfg.project_key) &&
      (!cfg.phase || d.phase === cfg.phase),
  );
  if (!ds.length) return planEmpty("deliverables");
  if (slide?._rows) ds = ds.slice(...slide._rows);
  return `<table class="tbl deliverables"><thead><tr><th>Deliverable</th><th>Owner</th><th>Planned</th><th>Status</th><th>Progress</th><th>Tasks</th><th>Hours</th></tr></thead><tbody>${ds
    .map(
      (d) =>
        `<tr data-status="${esc(d.status)}"><td>${esc(d.title)} <small>${esc(d.key)}</small></td><td>${esc(d.owner ?? "—")}</td><td>${day(d.planned_start)} → ${day(d.planned_end)}</td><td>${chip(d.status)}</td><td>${bar(d.progress_pct)}</td><td>${d.linked_tasks || "—"}</td><td>${d.hours_logged || "—"}</td></tr>`,
    )
    .join("")}</tbody></table>`;
}

export function renderPhaseStatus(ctx, cfg) {
  const phases = ctx.D.phases.filter(
    (p) => !cfg.project_key || p.project === cfg.project_key,
  );
  if (!phases.length) return planEmpty("phases");
  return `<div class="cards phases">${phases
    .map((p) => {
      const risks = ctx.D.risks.filter(
        (r) => r.project === p.project && r.status !== "closed",
      ).length;
      return `<section class="card phase" data-status="${esc(p.status)}"><h3>${esc(p.title)}</h3>
        <div>${chip(p.status)}</div>
        <div class="muted">${day(p.planned_start)} → ${day(p.planned_end)}</div>
        <div>${bar(p.progress_pct)}</div>
        <div class="muted">${p.deliverable_count} deliverable${p.deliverable_count === 1 ? "" : "s"} · ${risks} open risk${risks === 1 ? "" : "s"}</div></section>`;
    })
    .join("")}</div>`;
}

export function renderPlanVsActual(ctx, cfg, slide) {
  let ds = ctx.D.deliverables.filter(
    (d) => !cfg.project_key || d.project === cfg.project_key,
  );
  if (!ds.length) return planEmpty("deliverables");
  if (slide?._rows) ds = ds.slice(...slide._rows);
  const v = (n) =>
    n == null ? "—" : n === 0 ? "on plan" : `${n > 0 ? "+" : ""}${n} d`;
  return `<table class="tbl plan-vs-actual"><thead><tr><th>Deliverable</th><th>Baseline end</th><th>Current end</th><th>Variance</th><th>Actual</th><th>Status</th></tr></thead><tbody>${ds
    .map(
      (d) =>
        `<tr><td>${esc(d.title)}</td><td>${day(d.baseline_end)}</td><td>${day(d.planned_end)}</td><td class="${d.baseline_variance_days > 0 ? "sev-warning" : ""}">${v(d.baseline_variance_days)}</td><td>${day(d.actual_end)}</td><td>${chip(d.status)}</td></tr>`,
    )
    .join("")}</tbody></table>`;
}

const DAY = 86400000;
const t = (s) => Date.parse(s.slice(0, 10));

/** Phases and deliverables on a time axis: baseline ghost, milestone diamonds, dependency arrows, today line. */
export function renderRoadmap(ctx, cfg) {
  const { D } = ctx;
  const phases = D.phases.filter(
    (p) => !cfg.project_key || p.project === cfg.project_key,
  );
  if (!phases.length) return planEmpty("roadmap");
  const delivs = D.deliverables.filter((d) =>
    phases.some((p) => p.key === d.phase),
  );
  const rows = phases.flatMap((p) => [
    { kind: "phase", ...p },
    ...delivs
      .filter((d) => d.phase === p.key)
      .map((d) => ({ kind: "deliverable", ...d })),
  ]);
  const stamps = rows
    .flatMap((r) =>
      [r.planned_start, r.planned_end, r.baseline_end].filter(Boolean),
    )
    .concat(
      D.milestones
        .filter((m) => !cfg.project_key || m.project === cfg.project_key)
        .map((m) => m.due),
    );
  const lo = Math.min(...stamps.map(t));
  const hi = Math.max(...stamps.map(t), lo + 7 * DAY);
  const W = 1000,
    L = 230,
    ROW = 26,
    TOP = 28;
  const x = (s) => L + ((t(s) - lo) / (hi - lo)) * (W - L - 10);
  const H = TOP + rows.length * ROW + 20;
  const idx = new Map(rows.map((r, i) => [r.key, i]));
  const bars = rows
    .map((r, i) => {
      const y = TOP + i * ROW;
      const label = `<text x="${r.kind === "phase" ? 4 : 18}" y="${y + 15}" class="rm-label ${r.kind}">${esc(r.title.slice(0, 32))}</text>`;
      if (!r.planned_start || !r.planned_end) return label;
      const w = Math.max(3, x(r.planned_end) - x(r.planned_start));
      const ghost = r.baseline_end
        ? `<line x1="${x(r.baseline_end)}" x2="${x(r.baseline_end)}" y1="${y + 3}" y2="${y + 21}" class="rm-ghost"><title>Baseline end ${esc(r.baseline_end)}</title></line>`
        : "";
      const done =
        r.progress_pct != null
          ? `<rect x="${x(r.planned_start)}" y="${y + 7}" width="${(w * r.progress_pct) / 100}" height="10" class="rm-done"/>`
          : "";
      return `${label}<rect x="${x(r.planned_start)}" y="${y + 5}" width="${w}" height="14" rx="3" class="rm-bar ${r.kind} st-${esc(r.status)}"><title>${esc(r.title)} · ${esc(r.planned_start)} → ${esc(r.planned_end)} · ${esc(STATUS_LABEL[r.status] ?? r.status)}</title></rect>${done}${ghost}`;
    })
    .join("");
  const arrows = delivs
    .flatMap((d) => (d.depends_on ?? []).map((k) => [k, d.key]))
    .filter(([a, b]) => idx.has(a) && idx.has(b))
    .map(([a, b]) => {
      const A = rows[idx.get(a)],
        B = rows[idx.get(b)];
      if (!A.planned_end || !B.planned_start) return "";
      const x1 = x(A.planned_end),
        y1 = TOP + idx.get(a) * ROW + 12,
        x2 = x(B.planned_start),
        y2 = TOP + idx.get(b) * ROW + 12;
      return `<path d="M${x1},${y1} C${x1 + 14},${y1} ${x2 - 14},${y2} ${x2},${y2}" class="rm-dep" marker-end="url(#rm-arrow)"/>`;
    })
    .join("");
  const ms = D.milestones
    .filter((m) => !cfg.project_key || m.project === cfg.project_key)
    .map(
      (m) =>
        `<g class="rm-ms st-${esc(m.status)}"><path d="M${x(m.due)},${TOP - 14} l6,6 l-6,6 l-6,-6 z"/><title>${esc(m.name)} · ${esc(m.due)} · ${esc(STATUS_LABEL[m.status] ?? m.status)}</title></g>`,
    )
    .join("");
  const today = D.as_of.slice(0, 10);
  const todayX =
    t(today) >= lo && t(today) <= hi
      ? `<line x1="${x(today)}" x2="${x(today)}" y1="${TOP - 6}" y2="${H - 10}" class="rm-today"><title>Today</title></line>`
      : "";
  const months = [];
  for (
    let d = new Date(lo);
    d.getTime() <= hi;
    d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))
  ) {
    const s = d.toISOString().slice(0, 10);
    months.push(
      `<text x="${x(s)}" y="12" class="rm-month">${esc(s.slice(0, 7))}</text>`,
    );
  }
  const table = `<details class="rm-table"><summary>Table view</summary><table class="tbl roadmap-table"><thead><tr><th>Item</th><th>Start</th><th>End</th><th>Status</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.title)}</td><td>${day(r.planned_start)}</td><td>${day(r.planned_end)}</td><td>${chip(r.status)}</td></tr>`).join("")}</tbody></table></details>`;
  return `<figure class="roadmap"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Roadmap: ${phases.length} phases, ${delivs.length} deliverables, ${ms.length ? "milestones shown" : "no milestones"}"><defs><marker id="rm-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" class="rm-dep-head"/></marker></defs>${months.join("")}${todayX}${arrows}${bars}${ms}</svg>${table}</figure>`;
}
