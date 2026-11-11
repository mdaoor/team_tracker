import { drillAttr, esc, sev, teamColor, violationsOf } from "../util.js";

export function peopleList(ctx) {
  return ctx.D.people.filter((p) =>
    ctx.items.some((i) => i.assignee === p.handle),
  );
}
export function renderPeople(ctx, cfg, slide) {
  const { D, items } = ctx;
  const v = violationsOf(D, items);
  const ppl = peopleList(ctx);
  const shown = slide?._rows ? ppl.slice(slide._rows[0], slide._rows[1]) : ppl;
  return `<div class="card"><table><thead><tr><th>person</th><th>team</th><th>items</th><th>active</th><th>done</th><th>hours</th><th>Σw done</th><th>violations</th><th>habits</th></tr></thead><tbody>${shown
    .map((p) => {
      const mine = items.filter((i) => i.assignee === p.handle);
      const h = D.habits.filter((x) => x.subject === p.handle);
      return `<tr class="row" ${drillAttr(p.handle, { assignee: p.handle })} tabindex="0"><td>${esc(p.name)} <span class="mono" style="color:var(--text-3)">${esc(p.handle)}</span></td><td><i class="dot" style="display:inline-block;background:${teamColor(D, p.team)}"></i> ${esc(p.team ?? "—")}</td>
    <td>${mine.length}</td><td>${mine.filter((i) => i.state === "active").length}</td><td>${mine.filter((i) => i.state === "done").length}</td><td>${mine.reduce((s, i) => s + i.hours_logged, 0).toFixed(0)}</td><td>${mine.filter((i) => i.state === "done").reduce((s, i) => s + i.weight, 0)}</td>
    <td>${v.filter((x) => x.person === p.handle).length}</td><td>${h.map((x) => `<span class="pill ${x.polarity === "positive" ? "good" : "warn"}">${esc(x.key.replace(/_/g, " "))} ${x.trend === "improving" ? "↑" : x.trend === "worsening" ? "↓" : "→"}</span>`).join(" ")}</td></tr>`;
    })
    .join("")}</tbody></table></div>`;
}

const RESTRICTED = `<div class="card restricted">Restricted — you do not have access to this data.</div>`;

export function violationRows(ctx) {
  const v = violationsOf(ctx.D, ctx.items);
  const byCat = {};
  v.forEach((x) => (byCat[x.category] = byCat[x.category] || []).push(x));
  return { v, byCat };
}
export function renderViolations(ctx, cfg, slide) {
  if (ctx.restricted(slide?.id)) return RESTRICTED;
  const { v, byCat } = violationRows(ctx);
  const body =
    Object.entries(byCat)
      .sort((a, b) => b[1].length - a[1].length)
      .map(
        ([c, l]) =>
          `<div style="margin:8px 0 4px;font-weight:600">${esc(c)} <span style="color:var(--text-3);font-weight:400">${l.length}</span></div>` +
          l
            .slice(0, 6)
            .map(
              (x) =>
                `<div class="hab"><span><span class="pill ${sev(x.severity)}">${esc(x.severity)}</span> <span class="mono">${esc(x.target)}</span> ${esc(x.policy.replace(/_/g, " "))}</span><span style="color:var(--text-2)">${esc(x.detail)}${x.person ? " · " + esc(x.person) : ""}</span></div>`,
            )
            .join("") +
          (l.length > 6
            ? `<div class="hab" style="color:var(--text-3)">+${l.length - 6} more</div>`
            : ""),
      )
      .join("") || "<div>No open violations in scope.</div>";
  return `<div class="card" id="viol"><h3>Open violations <span class="k">${v.length}</span></h3>${body}</div>`;
}
export function habitList(ctx) {
  const ppl = new Set(ctx.items.map((i) => i.assignee));
  return ctx.D.habits
    .filter((h) => ppl.has(h.subject))
    .sort((a, b) => (a.polarity > b.polarity ? -1 : 1) || b.rate - a.rate);
}
export function renderHabits(ctx, cfg, slide) {
  if (ctx.restricted(slide?.id)) return RESTRICTED;
  const hab = habitList(ctx);
  return `<div class="card" id="habits"><h3>Habits <span class="k">${hab.length}</span></h3><div class="legend"><span><i style="background:var(--good)"></i>positive</span><span><i style="background:var(--warn)"></i>needs work</span></div>${
    hab
      .map(
        (h) =>
          `<div class="hab"><span><i class="dot" style="display:inline-block;background:${h.polarity === "positive" ? "var(--good)" : "var(--warn)"}"></i> ${esc(h.key.replace(/_/g, " "))} <span class="mono" style="color:var(--text-3)">${esc(h.subject)}</span></span><span>${Math.round(h.rate * 100)}% · ${esc(h.trend)}</span></div>`,
      )
      .join("") || "<div>No habits in scope.</div>"
  }</div>`;
}
/** The prototype's last section shows violations and habits side by side. */
export function renderViolHabits(ctx, cfg, slide) {
  return `<div class="grid2">${renderViolations(ctx, cfg, slide)}${renderHabits(ctx, cfg, slide)}</div>`;
}
