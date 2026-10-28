import {
  projectShown,
  applyFilter,
  drillAttr,
  esc,
  stuck,
  tableToggle,
  teamColor,
  violationsOf,
} from "../util.js";

export function renderKpis(ctx) {
  const { D, items } = ctx;
  const v = violationsOf(D, items);
  const done = items.filter((i) => i.state === "done");
  const onTime = done.filter(
    (i) => !i.actual_end || i.actual_end <= i.planned_end,
  );
  const hours = items.reduce((s, i) => s + i.hours_logged, 0);
  const wt = done.reduce((s, i) => s + i.weight, 0);
  const riskKeys = new Set(items.flatMap((i) => i.risks));
  const exposure = D.risks
    .filter((r) => riskKeys.has(r.key))
    .reduce((s, r) => s + r.p * r.i, 0);
  const n = (f) => applyFilter(items, f, D).length;
  const of = (f, what) => `${n(f)} item${n(f) === 1 ? "" : "s"} ${what}`;
  const tiles = [
    [
      "Open",
      items.filter((i) => i.state === "open").length,
      "not started",
      ["open", { state: "open" }],
    ],
    [
      "Active",
      items.filter((i) => i.state === "active").length,
      "in build / test / staging",
      ["active", { state: "active" }],
    ],
    ["Done", done.length, "in range", ["done", { state: "done" }]],
    [
      "Hours",
      hours.toFixed(0),
      "logged",
      [
        "with hours",
        { hours: true },
        `${hours.toFixed(0)} h logged: ${of({ hours: true }, "with time")}`,
      ],
    ],
    [
      "Weighted throughput",
      wt,
      "Σ weight of done",
      [
        "done",
        { state: "done" },
        `weight ${wt} over ${of({ state: "done" }, "done")}`,
      ],
    ],
    [
      "On-time",
      done.length ? Math.round((onTime.length / done.length) * 100) + "%" : "—",
      "of finished items",
      [
        "late",
        { late: true },
        `${n({ late: true })} of ${done.length} finished items were late`,
      ],
    ],
    [
      "Violations",
      v.length,
      "open",
      [
        "with violations",
        { violated: true },
        `${v.length} open violation${v.length === 1 ? "" : "s"} on ${of({ violated: true }, "")}`.trim(),
      ],
    ],
    [
      "Risk exposure",
      exposure,
      "Σ p×i of linked risks",
      [
        "with risk",
        { risk: true },
        `exposure ${exposure} (Σ p×i): ${of({ risk: true }, "linked to a risk")}`,
      ],
    ],
  ];
  return `<div class="kpis">${tiles
    .map(
      ([l, val, d, drill]) =>
        `<div class="kpi" ${drill ? drillAttr(drill[0], drill[1], drill[2]) : ""} tabindex="0" role="button"><div class="v">${val}</div><div class="l">${l}</div><div class="d">${d}</div></div>`,
    )
    .join("")}</div>`;
}

export function renderProjects(ctx, cfg, slide) {
  const { D, items } = ctx;
  const only = cfg.project_key;
  const projs = D.projects.filter(
    (p) => projectShown(ctx, p) && (!only || p.key === only),
  );
  const page = slide?._rows;
  const list = page ? projs.slice(page[0], page[1]) : projs;
  return (
    `<div class="grid2">` +
    (list
      .map((p) => {
        const its = items.filter((i) => i.project === p.key);
        const done = its.filter((i) => i.state === "done").length;
        const pct = its.length ? Math.round((done / its.length) * 100) : 0;
        const ms = D.milestones
          .filter((m) => m.project === p.key)
          .sort((a, b) => a.due.localeCompare(b.due))[0];
        const risk = D.risks
          .filter((r) => r.project === p.key)
          .sort((a, b) => b.p * b.i - a.p * a.i)[0];
        const nStuck = its.filter((i) => stuck(D, i)).length;
        const dist = D.stages.map(
          (s) => its.filter((i) => i.stage === s).length,
        );
        const mx = Math.max(1, ...dist);
        const spark = `<svg width="140" height="22" viewBox="0 0 140 22" aria-label="stage distribution">${dist
          .map(
            (n, k) =>
              `<rect x="${k * 20}" y="${22 - Math.max(2, (n / mx) * 20)}" width="16" height="${Math.max(2, (n / mx) * 20)}" rx="2" fill="var(--seq${Math.min(6, k + 1)})"><title>${esc(D.stages[k])}: ${n}</title></rect>`,
          )
          .join("")}</svg>`;
        const epics = D.epics
          .filter((e) => e.project === p.key)
          .map((e) => {
            const ei = its.filter((i) => i.epic === e.key);
            const ed = ei.filter((i) => i.state === "done").length;
            return `<div class="hab"><span class="mono">${esc(e.key)}</span><span>${esc(e.title)}</span><span>${ed}/${ei.length}</span></div>`;
          })
          .join("");
        return `<div class="card" data-project="${esc(p.key)}">
      <h3><span>${esc(p.name)}</span><span class="k">${esc(p.key)} · ${esc(p.methodology)}</span></h3>
      <p>${esc(p.summary)}</p>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="meta"><span><b>${pct}%</b> done (${done}/${its.length})</span><span><b>${nStuck}</b> stuck</span>
        ${ms ? `<span>next: <b>${esc(ms.name)}</b> ${esc(ms.due)}</span>` : ""}
        ${risk ? `<span class="pill ${risk.p * risk.i >= 15 ? "crit" : risk.p * risk.i >= 9 ? "serious" : "warn"}">⚠ ${esc(risk.key)} ${risk.p * risk.i}</span>` : ""}</div>
      <div class="meta">${spark}<span style="color:var(--text-3);font-size:11px">stage distribution ${esc(D.stages[0])}→${esc(D.stages.at(-1))}</span></div>
      <div style="margin-top:8px">${epics}</div>
      <div class="meta"><a href="#" ${drillAttr(p.key, { project: p.key })}>open items ›</a></div>
    </div>`;
      })
      .join("") || `<div class="card">No projects in scope.</div>`) +
    `</div>`
  );
}

export function renderPipeline(ctx, _cfg, slide) {
  const { D, items } = ctx;
  const wipBreach = new Set(
    D.violations
      .filter((v) => v.policy === "wip_breach")
      .map((v) => v.target.split("/")[1]),
  );
  const asTable = ctx.tableMode(slide?.id);
  const toggle = tableToggle(slide, asTable);
  if (asTable) {
    return `<div class="card">${toggle}<table><thead><tr><th>stage</th><th>items</th><th>Σ weight</th><th>over expected time</th><th>WIP</th></tr></thead><tbody>${D.stages
      .map((s) => {
        const its = items.filter((i) => i.stage === s);
        const w = its.reduce((a, i) => a + i.weight, 0);
        const over = its.filter((i) => stuck(D, i)).length;
        const wip = wipBreach.has(s);
        return `<tr class="${wip ? "over" : ""}"><th><a href="#" ${drillAttr("stage " + s, { stage: s })}>${esc(s)}</a></th><td>${its.length}</td><td>${+w.toFixed(2)}</td><td>${over ? `${over} over ${D.stage_expected_days[s]}d` : "—"}</td><td>${wip ? "breach" : "—"}</td></tr>`;
      })
      .join("")}</tbody></table></div>`;
  }
  return `${toggle}<div class="pipe">${D.stages
    .map((s) => {
      const its = items.filter((i) => i.stage === s);
      const w = its.reduce((a, i) => a + i.weight, 0);
      const over = its.filter((i) => stuck(D, i)).length;
      const wip = wipBreach.has(s);
      return `<div class="col ${wip ? "over" : ""}" data-stage="${esc(s)}" ${drillAttr("stage " + s, { stage: s })} tabindex="0" role="button">
      <div class="t">${esc(s)}</div><div class="n">${its.length}</div>
      <div class="w">Σw ${+w.toFixed(2)} · ${over ? `<span style="color:var(--serious)">${over} over ${D.stage_expected_days[s]}d</span>` : "—"}${wip ? " · WIP breach" : ""}</div>
      <div class="dots">${its.map((i) => `<span class="dot" style="background:${teamColor(D, i.team)}" title="${esc(i.key)} ${esc(i.title)} (${esc(i.team)})"></span>`).join("")}</div>
    </div>`;
    })
    .join("")}</div>`;
}
export { applyFilter };
