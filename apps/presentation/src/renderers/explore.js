import {
  drillAttr,
  esc,
  keysOf,
  measureOf,
  sortKeys,
  teamColor,
} from "../util.js";

const DIMS = [["assignee", "person"], ["team", "team"], ["project", "project"], ["product", "product"], ["stage", "stage"], ["priority", "priority"], ["epic", "epic"], ["tags", "tag"]]; // prettier-ignore
const MEASURES = [["count", "items"], ["hours", "hours logged"], ["wt", "weighted throughput (done)"], ["viol", "open violations"]]; // prettier-ignore

function matrix(ctx, a, b, m) {
  const { D, items } = ctx;
  const rows = sortKeys(D, new Set(items.flatMap((i) => keysOf(i, a))), a);
  const cols = b
    ? sortKeys(D, new Set(items.flatMap((i) => keysOf(i, b))), b)
    : null;
  const cells = rows.map((r) =>
    cols
      ? cols.map((c) =>
          measureOf(
            D,
            items.filter(
              (i) => keysOf(i, a).includes(r) && keysOf(i, b).includes(c),
            ),
            m,
          ),
        )
      : [
          measureOf(
            D,
            items.filter((i) => keysOf(i, a).includes(r)),
            m,
          ),
        ],
  );
  return { rows, cols, cells };
}

/** Bar chart (one dimension) or heat-map table (two); the `⇄` button swaps the chart for its table (a11y: every chart has one). */
export function renderExplorer(ctx, cfg, slide) {
  const { D } = ctx;
  const a = cfg.dimA, b = cfg.dimB || null, m = cfg.measure || "count"; // prettier-ignore
  const x = matrix(ctx, a, b, m);
  const asTable = ctx.tableMode(slide?.id);
  const controls = ctx.interactive
    ? `<div class="toolbar"><label>Rows</label><select data-ctl="dimA">${DIMS.map(([v, l]) => `<option value="${v}" ${v === a ? "selected" : ""}>${l}</option>`).join("")}</select>
       <label>Columns</label><select data-ctl="dimB"><option value="">— none —</option>${DIMS.map(([v, l]) => `<option value="${v}" ${v === b ? "selected" : ""}>${l}</option>`).join("")}</select>
       <label>Measure</label><select data-ctl="measure">${MEASURES.map(([v, l]) => `<option value="${v}" ${v === m ? "selected" : ""}>${l}</option>`).join("")}</select></div>`
    : "";
  const toggle = `<button class="ghost tbl-toggle" data-toggle="${esc(slide?.id ?? "")}" aria-pressed="${asTable}">${asTable ? "chart" : "table"} ⇄</button>`;
  if (!b && !asTable) {
    const vals = x.rows.map((r, k) => [r, x.cells[k][0]]);
    const max = Math.max(1, ...vals.map((v) => v[1]));
    const h = vals.length * 26 + 10, lw = 110, W = 760; // prettier-ignore
    return `${controls}<div class="card" id="explorer">${toggle}<svg width="100%" viewBox="0 0 ${W} ${h}" style="max-width:${W}px" role="img" aria-label="${esc(a)} by ${esc(m)}">${vals
      .map(([r, v], k) => {
        const bw = ((W - lw - 60) * v) / max;
        return `<g transform="translate(0,${k * 26 + 6})"><text x="${lw - 8}" y="13" text-anchor="end">${esc(r)}</text>
        <rect x="${lw}" y="2" width="${Math.max(2, bw)}" height="16" rx="4" fill="${a === "team" ? teamColor(D, r) : "var(--s1)"}" style="cursor:pointer" ${drillAttr(`${a}=${r}`, { dims: { [a]: r } })}></rect>
        <text x="${lw + bw + 6}" y="13" fill="var(--text)">${v}</text></g>`;
      })
      .join("")}</svg></div>`;
  }
  if (!b) {
    return `${controls}<div class="card" id="explorer">${toggle}<table><thead><tr><th>${esc(a)}</th><th>${esc(m)}</th></tr></thead><tbody>${x.rows.map((r, k) => `<tr><th>${esc(r)}</th><td>${x.cells[k][0]}</td></tr>`).join("")}</tbody></table></div>`;
  }
  const max = Math.max(1, ...x.cells.flat());
  const seq = (v) =>
    v === 0
      ? "var(--surface)"
      : `var(--seq${Math.min(6, 1 + Math.floor((v / max) * 5.999))})`;
  return `${controls}<div class="card" id="explorer"><div style="overflow:auto"><table><thead><tr><th>${esc(a)} \\ ${esc(b)}</th>${x.cols.map((c) => `<th>${esc(c)}</th>`).join("")}<th>total</th></tr></thead><tbody>${x.rows
    .map(
      (r, ri) =>
        `<tr><th>${esc(r)}</th>${x.cols.map((c, ci) => `<td style="background:${seq(x.cells[ri][ci])};color:${x.cells[ri][ci] / max > 0.5 ? "#fff" : "var(--text)"};cursor:pointer;text-align:center" data-r="${esc(r)}" data-c="${esc(c)}" ${x.cells[ri][ci] ? drillAttr(`${a}=${r} × ${b}=${c}`, { dims: { [a]: r, [b]: c } }) : ""}>${x.cells[ri][ci] || ""}</td>`).join("")}<td><b>${x.cells[ri].reduce((s, v) => s + v, 0)}</b></td></tr>`,
    )
    .join(
      "",
    )}</tbody></table></div><div class="legend" style="margin-top:8px">${[1, 2, 3, 4, 5, 6].map((k) => `<span><i style="background:var(--seq${k})"></i></span>`).join("")}<span>low → high ${esc(m)}</span></div></div>`;
}

/** Aggregate chart slide: the server already computed it (ACL applied), so numbers equal the view's. */
export function renderChart(ctx, cfg, slide) {
  const d = ctx.slideData[slide.id];
  if (!d || d.restricted)
    return `<div class="card">Restricted — you do not have access to this data.</div>`;
  const max = Math.max(1, ...d.cells.flat());
  const seq = (v) =>
    v === 0
      ? "var(--surface)"
      : `var(--seq${Math.min(6, 1 + Math.floor((v / max) * 5.999))})`;
  if (!d.cols || !d.cols.length) {
    const asTable = ctx.tableMode(slide.id);
    const toggle = `<button class="ghost tbl-toggle" data-toggle="${esc(slide.id)}" aria-pressed="${asTable}">${asTable ? "chart" : "table"} ⇄</button>`;
    if (asTable)
      return `<div class="card">${toggle}<table><thead><tr><th>${esc(cfg.rows)}</th><th>${esc(d.measure)}</th></tr></thead><tbody>${d.rows.map((r, k) => `<tr><th>${esc(r.value)}</th><td>${d.cells[k][0]}</td></tr>`).join("")}</tbody></table></div>`;
    const h = d.rows.length * 26 + 10, lw = 130, W = 760; // prettier-ignore
    return `<div class="card">${toggle}<svg width="100%" viewBox="0 0 ${W} ${h}" style="max-width:${W}px" role="img" aria-label="${esc(cfg.rows)} by ${esc(d.measure)}">${d.rows
      .map((r, k) => {
        const v = d.cells[k][0];
        const bw = ((W - lw - 60) * v) / max;
        return `<g transform="translate(0,${k * 26 + 6})"><text x="${lw - 8}" y="13" text-anchor="end">${esc(r.value)}</text><rect x="${lw}" y="2" width="${Math.max(2, bw)}" height="16" rx="4" fill="var(--s1)"></rect><text x="${lw + bw + 6}" y="13" fill="var(--text)">${v}</text></g>`;
      })
      .join("")}</svg></div>`;
  }
  return `<div class="card" data-chart="matrix"><div style="overflow:auto"><table><thead><tr><th>${esc(cfg.rows)} \\ ${esc(cfg.cols)}</th>${d.cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${d.rows
    .map(
      (r, ri) =>
        `<tr><th>${esc(r.value)}</th>${d.cols.map((c, ci) => `<td style="background:${seq(d.cells[ri][ci])};color:${d.cells[ri][ci] / max > 0.5 ? "#fff" : "var(--text)"};text-align:center" data-cell="${esc(r.value)}|${esc(c)}">${d.cells[ri][ci] || ""}</td>`).join("")}</tr>`,
    )
    .join("")}</tbody></table></div></div>`;
}

export function itemsTable(ctx, items) {
  const { D } = ctx;
  return `<div class="card" style="padding:0;overflow:auto"><table><thead><tr><th>key</th><th>title</th><th>project</th><th>owner</th><th>stage</th><th>prio</th><th>w</th><th>est</th><th>hrs</th><th>plan end</th><th>tags</th></tr></thead><tbody>${items
    .map(
      (i) =>
        `<tr class="row" data-open="${esc(i.key)}" tabindex="0"><td class="mono">${esc(i.key)}</td><td>${esc(i.title)}</td><td>${esc(i.project)}</td><td>${esc(i.assignee ?? "—")}</td><td>${esc(i.stage ?? "—")} <span style="color:${i.state !== "done" && i.stage_age_days > (D.stage_expected_days[i.stage] ?? 1e9) ? "var(--serious)" : "var(--text-3)"}">${i.stage_age_days ?? 0}d</span></td><td>${esc(i.priority)}</td><td>${i.weight}</td><td>${i.estimate_h ?? '<span class="pill warn">—</span>'}</td><td>${i.hours_logged}</td><td>${esc(i.planned_end ?? "—")}</td><td>${(i.tags ?? []).map((t) => "#" + esc(t)).join(" ")}</td></tr>`,
    )
    .join("")}</tbody></table></div>`;
}

/** `table` and `view` slides: query results frozen (or live) from the product's own query API. */
export function renderQueryItems(ctx, cfg, slide) {
  const d = ctx.slideData[slide.id];
  if (!d || d.restricted)
    return `<div class="card">Restricted — you do not have access to this data.</div>`;
  const all = d.items ?? [];
  const rows = slide._rows ? all.slice(slide._rows[0], slide._rows[1]) : all;
  const head = d.view
    ? `<p class="s-note">${esc(d.view.name)} · ${esc(d.layout)} · ${all.length} items</p>`
    : `<p class="s-note">${all.length} items</p>`;
  return head + itemsTable(ctx, rows);
}
