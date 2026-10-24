import { drillAttr, esc, teamColor } from "../util.js";

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
  return `<div class="card">${overdue.length ? `<div class="meta" style="margin:0 0 10px"><span class="pill crit">${overdue.length} overdue</span><a href="#" ${drillAttr("overdue", { overdue: today })}>show ›</a></div>` : ""}
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

export function renderGantt(ctx, cfg, slide) {
  const { D, items } = ctx;
  const epics = D.epics.filter((e) => items.some((i) => i.epic === e.key));
  const rows = epics
    .map((e) => {
      const ei = items.filter((i) => i.epic === e.key);
      return {
        e,
        start: ei
          .map((i) => i.planned_start)
          .filter(Boolean)
          .sort()[0],
        end: ei
          .map((i) => i.planned_end)
          .filter(Boolean)
          .sort()
          .slice(-1)[0],
        done: ei.filter((i) => i.state === "done").length,
        n: ei.length,
        project: e.project,
      };
    })
    .filter((r) => r.start);
  const ms = D.milestones.filter((m) =>
    items.some((i) => i.project === m.project),
  );
  const asTable = ctx.tableMode(slide?.id);
  const toggle = `<button class="ghost tbl-toggle" data-toggle="${esc(slide?.id ?? "")}" aria-pressed="${asTable}">${asTable ? "chart" : "table"} ⇄</button>`;
  if (!rows.length)
    return `<div class="card">No scheduled epics in scope.</div>`;
  if (asTable) {
    return `<div class="card">${toggle}<table><thead><tr><th>epic</th><th>start</th><th>end</th><th>done</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.e.key)} ${esc(r.e.title)}</td><td>${r.start}</td><td>${r.end}</td><td>${r.done}/${r.n}</td></tr>`).join("")}${ms.map((m) => `<tr><td>◆ ${esc(m.name)}</td><td colspan="2">${m.due}</td><td>${esc(m.project)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  const dates = [
    ...rows.flatMap((r) => [r.start, r.end]),
    ...ms.map((m) => m.due),
    D.as_of.slice(0, 10),
  ].sort();
  const d0 = new Date(dates[0]), d1 = new Date(dates.at(-1)); // prettier-ignore
  d0.setDate(d0.getDate() - 2);
  d1.setDate(d1.getDate() + 3);
  const span = (d1 - d0) / 864e5, lw = 220, W = 900, px = (W - lw - 20) / span; // prettier-ignore
  const x = (d) => lw + ((new Date(d) - d0) / 864e5) * px;
  const H = rows.length * 26 + 40;
  let g = `<svg width="100%" viewBox="0 0 ${W} ${H}" style="max-width:${W}px" role="img" aria-label="Gantt chart">`;
  for (let t = new Date(d0); t <= d1; t.setDate(t.getDate() + 1))
    if (t.getDay() === 1)
      g += `<line x1="${x(t)}" x2="${x(t)}" y1="0" y2="${H - 20}" stroke="var(--line)"/><text x="${x(t) + 3}" y="${H - 6}">${t.toISOString().slice(5, 10)}</text>`;
  const tx = x(D.as_of.slice(0, 10));
  g += `<line x1="${tx}" x2="${tx}" y1="0" y2="${H - 20}" stroke="var(--s8)" stroke-dasharray="3 3"/><text x="${tx + 3}" y="10" fill="var(--s8)">today</text>`;
  rows.forEach((r, k) => {
    const y = k * 26 + 16, x0 = x(r.start), x1 = x(r.end), w = Math.max(4, x1 - x0); // prettier-ignore
    const team = D.projects.find((p) => p.key === r.project)?.team;
    g += `<text x="${lw - 8}" y="${y + 12}" text-anchor="end">${esc(r.e.key)} ${esc(r.e.title).slice(0, 28)}</text>
    <rect x="${x0}" y="${y}" width="${w}" height="14" rx="4" fill="var(--line)"/><rect x="${x0}" y="${y}" width="${(w * r.done) / Math.max(1, r.n)}" height="14" rx="4" fill="${teamColor(D, team)}"/>
    <text x="${x1 + 6}" y="${y + 11}" fill="var(--text)">${r.done}/${r.n}</text>`;
  });
  ms.forEach((m) => {
    const mx = x(m.due);
    g += `<polygon points="${mx},${H - 32} ${mx + 6},${H - 26} ${mx},${H - 20} ${mx - 6},${H - 26}" fill="var(--s7)"><title>${esc(m.name)} ${m.due}</title></polygon>`;
  });
  g += "</svg>";
  return `<div class="card">${toggle}${g}<div class="legend">${D.teams.map((t) => `<span><i style="background:${teamColor(D, t)}"></i>${esc(t)}</span>`).join("")}<span><i style="background:var(--s7)"></i>milestone</span><span><i style="background:var(--line)"></i>remaining</span></div></div>`;
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
