import { ganttSvg, ganttTable, layoutGantt, snapshotRows } from "../gantt.js";
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

const ROW_KINDS = [["phase", "Phases & deliverables"], ["epic", "Epics"], ["milestone", "Milestones"]]; // prettier-ignore
const ZOOM_LABELS = [["fit", "Fit"], ["month", "Month"], ["week", "Week"], ["day", "Day"]]; // prettier-ignore

/** The roadmap: `snapshotRows` + `layoutGantt` + `ganttSvg` from ../gantt.js, the single Gantt implementation (R6.4).
 *  `cfg.rows` = phase (default) | epic | milestone, `cfg.zoom` = fit (default) | month | week | day; page mode shows both as selects. */
export function renderRoadmap(ctx, cfg, slide) {
  const { D } = ctx;
  const kind = ROW_KINDS.some(([k]) => k === cfg.rows) ? cfg.rows : "phase";
  const zoom = ZOOM_LABELS.some(([k]) => k === cfg.zoom) ? cfg.zoom : "fit";
  const { rows, deps } = snapshotRows(D, ctx.items ?? [], {
    rows: kind,
    project_key: cfg.project_key,
  });
  if (!rows.length)
    return planEmpty(kind === "phase" ? "roadmap" : `${kind} schedule`);
  const milestones = D.milestones.filter(
    (m) => !cfg.project_key || m.project === cfg.project_key,
  );
  const model = layoutGantt({
    rows,
    deps,
    milestones,
    today: D.as_of.slice(0, 10),
    zoom,
  });
  const nMs = milestones.length;
  const label = `Roadmap: ${rows.length} rows, ${deps.length} dependenc${deps.length === 1 ? "y" : "ies"}, ${nMs ? `${nMs} milestone${nMs === 1 ? "" : "s"} shown` : "no milestones"}`;
  const opt = (list, cur) =>
    list
      .map(
        ([v, l]) =>
          `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`,
      )
      .join("");
  const controls = ctx.interactive
    ? `<div class="toolbar"><label>Rows</label><select data-ctl="rows">${opt(ROW_KINDS, kind)}</select><label>Zoom</label><select data-ctl="zoom">${opt(ZOOM_LABELS, zoom)}</select></div>`
    : "";
  const svg = ganttSvg(model, { label });
  return `<figure class="roadmap" data-rows="${kind}" data-zoom="${zoom}">${controls}${model.scroll ? `<div class="rm-scroll" role="region" aria-label="Roadmap, scrollable" tabindex="0">${svg}</div>` : svg}<details class="rm-table"><summary>Table view</summary>${ganttTable(model, rows)}</details></figure>`;
}
