import { ganttSvg, ganttTable, layoutGantt, snapshotRows } from "../gantt.js";
import { drillAttr, esc, tableToggle, teamColor } from "../util.js";

const GANTT_ZOOMS = ["fit", "month", "week", "day"];

export function renderRadar(ctx, cfg, slide) {
  const { D, items } = ctx;
  const today = D.as_of.slice(0, 10);
  const horizon = new Date(today);
  horizon.setDate(horizon.getDate() + (cfg.days ?? 14));
  const hz = horizon.toISOString().slice(0, 10);
  const due = items
    .filter(
      (i) =>
        i.state !== "done" &&
        i.planned_end &&
        i.planned_end >= today &&
        i.planned_end <= hz,
    )
    .sort((a, b) => a.planned_end.localeCompare(b.planned_end));
  const overdue = items.filter(
    (i) => i.state !== "done" && i.planned_end && i.planned_end < today,
  );
  const shown = slide?._rows ? due.slice(slide._rows[0], slide._rows[1]) : due;
  const byDay = {};
  shown.forEach((i) =>
    (byDay[i.planned_end] = byDay[i.planned_end] || []).push(i),
  );
  const row = (i) =>
    `<tr class="row" data-open="${esc(i.key)}" tabindex="0"><td class="mono">${esc(i.key)}</td><td>${esc(i.title)}</td><td>${esc(i.assignee ?? "—")}</td><td>${esc(i.stage)} <span style="color:var(--text-3)">${i.stage_age_days}d</span></td><td>${i.risks.map((r) => `<span class="pill serious">⚠ ${esc(r)}</span>`).join(" ")}${i.estimate_h == null ? '<span class="pill warn">no est.</span>' : ""}</td></tr>`;
  const asTable = ctx.tableMode(slide?.id);
  const toggle = tableToggle(slide, asTable);
  if (asTable) {
    const flat = [...overdue, ...shown].sort((a, b) =>
      a.planned_end.localeCompare(b.planned_end),
    );
    return `<div class="card">${toggle}<table><thead><tr><th>due</th><th>key</th><th>item</th><th>owner</th><th>stage</th><th>flags</th></tr></thead><tbody>${
      flat
        .map(
          (i) =>
            `<tr class="row" data-open="${esc(i.key)}" tabindex="0"><td>${i.planned_end}${i.planned_end < today ? ' <span class="pill crit">overdue</span>' : ""}</td><td class="mono">${esc(i.key)}</td><td>${esc(i.title)}</td><td>${esc(i.assignee ?? "—")}</td><td>${esc(i.stage)} ${i.stage_age_days}d</td><td>${i.risks.map((r) => `<span class="pill serious">⚠ ${esc(r)}</span>`).join(" ")}${i.estimate_h == null ? '<span class="pill warn">no est.</span>' : ""}</td></tr>`,
        )
        .join("") ||
      `<tr><td colspan="6">Nothing planned in the next ${cfg.days ?? 14} days.</td></tr>`
    }</tbody></table></div>`;
  }
  return `<div class="card">${toggle}${overdue.length ? `<div class="meta" style="margin:0 0 10px"><span class="pill crit">${overdue.length} overdue</span><a href="#" ${drillAttr("overdue", { overdue: today })}>show ›</a></div>` : ""}
  <table><thead><tr><th>key</th><th>item</th><th>owner</th><th>stage</th><th>flags</th></tr></thead><tbody>${
    Object.keys(byDay)
      .sort()
      .map(
        (d) =>
          `<tr><td colspan="5" style="background:var(--surface);font-weight:600;color:var(--text-2)">${d} · ${byDay[d].length} item${byDay[d].length > 1 ? "s" : ""}</td></tr>` +
          byDay[d].map(row).join(""),
      )
      .join("") ||
    `<tr><td colspan="5">Nothing planned in the next ${cfg.days ?? 14} days.</td></tr>`
  }</tbody></table></div>`;
}
export const radarRows = (ctx, cfg) => {
  const today = ctx.D.as_of.slice(0, 10);
  const horizon = new Date(today);
  horizon.setDate(horizon.getDate() + (cfg.days ?? 14));
  const hz = horizon.toISOString().slice(0, 10);
  return ctx.items.filter(
    (i) =>
      i.state !== "done" &&
      i.planned_end &&
      i.planned_end >= today &&
      i.planned_end <= hz,
  ).length;
};

const GANTT_ROWS = ["epic", "item", "milestone"];

/**
 * The `gantt` slide: epics (default), items or milestones drawn by the shared gantt.js (R6.4), the same module as the Roadmap
 * slide. `cfg.rows` = epic | item | milestone, `cfg.baseline` ghosts the original end where an item carries `baseline_end`.
 * The `table ⇄` toggle shows the rows as a table plus the milestones.
 */
export function renderGantt(ctx, cfg, slide) {
  const { D, items } = ctx;
  const kind = GANTT_ROWS.includes(cfg.rows) ? cfg.rows : "epic";
  const { rows, deps } = snapshotRows(D, items, {
    rows: kind,
    project_key: cfg.project_key,
    baseline: !!cfg.baseline,
  });
  if (!rows.length)
    return `<div class="card">No scheduled ${kind === "epic" ? "epics" : kind === "item" ? "items" : "milestones"} in scope.</div>`;
  const projects = new Set(items.map((i) => i.project));
  const milestones = D.milestones.filter((m) => projects.has(m.project));
  const asTable = ctx.tableMode(slide?.id);
  const toggle = tableToggle(slide, asTable);
  const model = layoutGantt({
    rows,
    deps,
    milestones: kind === "milestone" ? [] : milestones,
    today: D.as_of.slice(0, 10),
    zoom: GANTT_ZOOMS.includes(cfg.zoom) ? cfg.zoom : "fit",
    width: 900,
    labelWidth: 220,
    axisHeight: 30,
  });
  if (asTable) {
    const ms = kind === "milestone" ? [] : milestones;
    return `<div class="card">${toggle}${ganttTable(model, rows).replace("</tbody>", ms.map((m) => `<tr><td>◆ ${esc(m.name)}</td><td colspan="2">${esc(m.due)}</td><td>${esc(m.project)}</td></tr>`).join("") + "</tbody>")}</div>`;
  }
  const svg = ganttSvg(model, {
    label: `Gantt chart: ${rows.length} ${kind} row${rows.length === 1 ? "" : "s"}`,
  });
  const legend = `<div class="legend"><span><i class="rm-bar-key"></i>${kind} span</span><span><i class="rm-done-key"></i>done share</span><span><i class="rm-ms-key"></i>milestone</span><span><i class="rm-today-key"></i>today</span></div>`;
  return `<figure class="card roadmap gantt-slide" data-rows="${kind}" data-zoom="fit">${toggle}${svg}${legend}</figure>`;
}

export function renderNow(ctx) {
  const keys = new Set(ctx.items.map((i) => i.key));
  const now = ctx.D.now.filter((n) => keys.has(n.item));
  return (
    `<div class="list">` +
    (now
      .map(
        (n) =>
          `<div class="item" data-open="${esc(n.item)}" tabindex="0"><span class="mono">${esc(n.person)}</span><span>${esc(n.title)} <span class="t">· ${esc(n.project)} · ${esc(n.kind)}</span></span><span class="t">${n.since_min} min</span></div>`,
      )
      .join("") || `<div class="card">No active sessions in scope.</div>`) +
    `</div>`
  );
}
