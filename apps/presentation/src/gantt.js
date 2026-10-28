import { esc } from "./util.js";

/**
 * The one Gantt: pure layout + SVG string, no DOM and no framework. The deck's Roadmap slide draws with it, and the web console's
 * Views -> Gantt can import it (`dist/gantt.mjs`, see `build.mjs`) instead of keeping a second implementation (plan 15 R6.4).
 *
 * A row is `{key, title, planned_start, planned_end, type?, status?, state_category?, progress_pct?, rollup?, depth?,
 * baseline?: {planned_start?, planned_end}}`; a dependency is `{from, to}` (row keys); a milestone is `{name, due, project?, status?}`.
 * Dates are `YYYY-MM-DD`. Rows without both dates are listed (label only) but get no bar.
 */

const DAY = 86_400_000;
export const ZOOMS = ["day", "week", "month", "fit"];
/** Pixels per day for the scrolling zooms; `fit` stretches the chart to `width` instead. */
const PX = { day: 28, week: 8, month: 2.6 };
const ms = (d) => Date.parse(String(d).slice(0, 10) + "T00:00:00Z");
const iso = (t) => new Date(t).toISOString().slice(0, 10);
const DONE = new Set(["done", "completed"]);

/**
 * Pure layout. All coordinates are pixels of the SVG viewBox, so drawing is a map over the result.
 * @param {{rows: object[], deps?: {from:string,to:string}[], milestones?: object[], today?: string|null,
 *   zoom?: "day"|"week"|"month"|"fit", width?: number, labelWidth?: number, rowHeight?: number, axisHeight?: number}} o
 */
export function layoutGantt({
  rows,
  deps = [],
  milestones = [],
  today = null,
  zoom = "fit",
  width = 1000,
  labelWidth = 230,
  rowHeight = 26,
  axisHeight = 28,
}) {
  const z = ZOOMS.includes(zoom) ? zoom : "fit";
  const dated = (r) => !!(r.planned_start && r.planned_end);
  const points = [
    ...rows
      .filter(dated)
      .flatMap((r) => [
        ms(r.planned_start),
        ms(r.planned_end),
        ...(r.baseline?.planned_start ? [ms(r.baseline.planned_start)] : []),
        ...(r.baseline?.planned_end ? [ms(r.baseline.planned_end)] : []),
      ]),
    ...milestones.filter((m) => m.due).map((m) => ms(m.due)),
    ...(today ? [ms(today)] : []),
  ];
  const base = {
    labelWidth,
    rowHeight,
    axisHeight,
    zoom: z,
    bars: [],
    ticks: [],
    todayX: null,
    milestones: [],
    arrows: [],
  };
  if (!points.length)
    return { ...base, width: labelWidth, height: axisHeight, scroll: false };
  const lo = Math.min(...points) - 3 * DAY;
  const hi = Math.max(...points) + 7 * DAY;
  const days = (hi - lo) / DAY;
  const px = z === "fit" ? (width - labelWidth - 10) / days : PX[z];
  const X = (t) => labelWidth + ((t - lo) / DAY) * px;
  const bars = rows.map((r, i) => {
    const y = axisHeight + i * rowHeight;
    const done =
      r.state_category === "done" ||
      DONE.has(r.status) ||
      (r.progress_pct ?? 0) >= 100;
    const bar = {
      key: r.key,
      title: r.title ?? r.key,
      type: r.type ?? "item",
      status: r.status ?? null,
      depth: r.depth ?? 0,
      y,
      dated: dated(r),
      done,
      rollup: !!r.rollup,
      progress: r.progress_pct ?? null,
      x: 0,
      w: 0,
    };
    if (!bar.dated) return bar;
    bar.x = X(ms(r.planned_start));
    bar.w = Math.max(3, X(ms(r.planned_end) + DAY) - bar.x);
    const b = r.baseline;
    if (b?.planned_end) {
      bar.ghost = b.planned_start
        ? {
            x: X(ms(b.planned_start)),
            w: Math.max(2, X(ms(b.planned_end) + DAY) - X(ms(b.planned_start))),
          }
        : { x: X(ms(b.planned_end) + DAY), w: 0 }; // end-only baseline: a tick
      bar.slip = ms(r.planned_end) > ms(b.planned_end);
    }
    return bar;
  });
  const ticks = [];
  const tickMode = z === "fit" ? (days <= 70 ? "week" : "month") : z;
  for (
    let t = lo - ((new Date(lo).getUTCDay() + 6) % 7) * DAY;
    t <= hi;
    t += DAY
  ) {
    const d = new Date(t);
    if (t < lo - 6 * DAY) continue;
    if (tickMode === "day")
      ticks.push({
        x: X(t),
        label: String(d.getUTCDate()),
        major: d.getUTCDate() === 1 || d.getUTCDay() === 1,
      });
    else if (tickMode === "week" && d.getUTCDay() === 1)
      ticks.push({
        x: X(t),
        label: iso(t).slice(5),
        major: d.getUTCDate() <= 7,
      });
    else if (tickMode === "month" && d.getUTCDate() === 1)
      ticks.push({
        x: X(t),
        label: d.toLocaleString("en", {
          month: "short",
          year: "2-digit",
          timeZone: "UTC",
        }),
        major: d.getUTCMonth() === 0,
      });
  }
  const byKey = new Map(bars.map((b) => [b.key, b]));
  const arrows = [];
  for (const d of deps) {
    const a = byKey.get(d.from),
      b = byKey.get(d.to);
    if (!a?.dated || !b?.dated) continue;
    const x1 = a.x + a.w,
      y1 = a.y + rowHeight / 2,
      x2 = b.x,
      y2 = b.y + rowHeight / 2;
    const mid = Math.max(x1 + 6, x2 - 6);
    arrows.push({
      from: d.from,
      to: d.to,
      d: `M${x1},${y1} H${mid} V${y2} H${x2}`,
    });
  }
  return {
    ...base,
    width: z === "fit" ? width : X(hi) + 20,
    height: axisHeight + rows.length * rowHeight + 10,
    scroll: z !== "fit",
    bars,
    ticks,
    todayX: today ? X(ms(today)) : null,
    milestones: milestones
      .filter((m) => m.due)
      .map((m) => ({
        x: X(ms(m.due)),
        name: m.name,
        due: String(m.due).slice(0, 10),
        project: m.project ?? "",
        status: m.status ?? "",
      })),
    arrows,
  };
}

const STATUS_NAME = {
  not_started: "Not started",
  in_progress: "In progress",
  done: "Done",
  completed: "Completed",
  overdue: "Overdue",
  at_risk: "At risk",
  due_soon: "Due soon",
  planned: "Planned",
};
export const statusName = (s) => STATUS_NAME[s] ?? s;

/**
 * The SVG for a layout. Class names (`rm-*`) are the deck's roadmap styles; every number is finite by construction.
 * `label` is the accessible name; a table alternative is the caller's job (`ganttTable`).
 */
export function ganttSvg(model, { label = "Gantt chart" } = {}) {
  const m = model;
  const size = m.scroll
    ? ` width="${m.width.toFixed(0)}" height="${m.height.toFixed(0)}"`
    : "";
  const axis = m.axisHeight;
  const body = [];
  for (const t of m.ticks)
    body.push(
      `<line x1="${t.x.toFixed(1)}" x2="${t.x.toFixed(1)}" y1="${axis - 6}" y2="${m.height - 6}" class="rm-grid${t.major ? " major" : ""}"/><text x="${(t.x + 3).toFixed(1)}" y="12" class="rm-month">${esc(t.label)}</text>`,
    );
  if (m.todayX != null)
    body.push(
      `<line x1="${m.todayX.toFixed(1)}" x2="${m.todayX.toFixed(1)}" y1="${axis - 6}" y2="${m.height - 6}" class="rm-today"><title>Today</title></line><text x="${(m.todayX + 3).toFixed(1)}" y="${axis - 8}" class="rm-todaylbl">today</text>`,
    );
  for (const a of m.arrows)
    body.push(
      `<path d="${a.d}" class="rm-dep" data-from="${esc(a.from)}" data-to="${esc(a.to)}" marker-end="url(#rm-arrow)"/>`,
    );
  for (const b of m.bars) {
    const lx = 4 + b.depth * 14;
    const lbl = `<text x="${lx}" y="${b.y + 15}" class="rm-label ${esc(b.type)}">${esc(String(b.title).slice(0, 32 - b.depth * 2))}</text>`;
    if (!b.dated) {
      body.push(lbl);
      continue;
    }
    const ghost = b.ghost
      ? b.ghost.w
        ? `<rect x="${b.ghost.x.toFixed(1)}" y="${b.y + 20}" width="${b.ghost.w.toFixed(1)}" height="3" class="rm-ghost-bar"><title>Baseline</title></rect>`
        : `<line x1="${b.ghost.x.toFixed(1)}" x2="${b.ghost.x.toFixed(1)}" y1="${b.y + 3}" y2="${b.y + 21}" class="rm-ghost"><title>Baseline end</title></line>`
      : "";
    const prog =
      b.progress != null
        ? `<rect x="${b.x.toFixed(1)}" y="${b.y + 7}" width="${((b.w * b.progress) / 100).toFixed(1)}" height="10" class="rm-done"/>`
        : "";
    const st = b.status ? ` st-${esc(b.status)}` : b.done ? " st-done" : "";
    body.push(
      `${lbl}<rect x="${b.x.toFixed(1)}" y="${b.y + 5}" width="${b.w.toFixed(1)}" height="14" rx="3" class="rm-bar ${esc(b.type)}${st}${b.rollup ? " rollup" : ""}${b.slip ? " slip" : ""}" data-key="${esc(b.key)}"><title>${esc(b.title)}${b.status ? " · " + esc(statusName(b.status)) : ""}${b.slip ? " · slipped vs baseline" : ""}</title></rect>${prog}${ghost}`,
    );
  }
  for (const ms_ of m.milestones)
    body.push(
      `<g class="rm-ms${ms_.status ? " st-" + esc(ms_.status) : ""}"><path d="M${ms_.x.toFixed(1)},${axis - 14} l6,6 l-6,6 l-6,-6 z"/><title>${esc(ms_.name)} · ${esc(ms_.due)}${ms_.status ? " · " + esc(statusName(ms_.status)) : ""}</title></g>`,
    );
  return `<svg viewBox="0 0 ${m.width.toFixed(0)} ${m.height.toFixed(0)}"${size} role="img" aria-label="${esc(label)}" class="rm-svg"><defs><marker id="rm-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" class="rm-dep-head"/></marker></defs>${body.join("")}</svg>`;
}

/** Accessible alternative: the same rows as a table (every chart has one). */
export function ganttTable(model, rows) {
  const day = (d) => (d ? esc(String(d).slice(0, 10)) : "—");
  return `<table class="tbl roadmap-table"><thead><tr><th>Item</th><th>Start</th><th>End</th><th>Status</th></tr></thead><tbody>${rows
    .map(
      (r) =>
        `<tr><td>${esc(r.title ?? r.key)}</td><td>${day(r.planned_start)}</td><td>${day(r.planned_end)}</td><td>${r.status ? esc(statusName(r.status)) : r.state_category ? esc(r.state_category) : "—"}</td></tr>`,
    )
    .join("")}</tbody></table>`;
}

/**
 * Rows for a deck/page snapshot, by `rows` kind (the same names the web query uses):
 *  - `phase` (default for the plan Roadmap): each phase followed by its deliverables, dependencies from `depends_on`;
 *  - `item`: each scheduled item as its own bar;
 *  - `epic`: each epic spanning its items' planned dates, progress = done / total;
 *  - `milestone`: each milestone as a bar from the earliest planned start of its project to its due date.
 */
export function snapshotRows(
  D,
  items,
  { rows = "phase", project_key = null, baseline = false } = {},
) {
  const inProject = (x) => !project_key || x.project === project_key;
  // `baseline`: items may carry `baseline_end` (the end the plan first promised); epics take the latest of their items
  const ghost = (end) =>
    baseline && end ? { baseline: { planned_end: end } } : {};
  if (rows === "item") {
    const out = items
      .filter((i) => inProject(i) && i.planned_start && i.planned_end)
      .sort(
        (a, b) =>
          a.planned_start.localeCompare(b.planned_start) ||
          a.key.localeCompare(b.key),
      )
      .map((i) => ({
        key: i.key,
        title: `${i.key} ${i.title}`,
        type: "item",
        status: i.state === "done" ? "done" : undefined,
        planned_start: i.planned_start,
        planned_end: i.planned_end,
        progress_pct: i.state === "done" ? 100 : null,
        ...ghost(i.baseline_end),
      }));
    return { rows: out, deps: [] };
  }
  if (rows === "epic") {
    const out = D.epics.filter(inProject).flatMap((e) => {
      const its = items.filter((i) => i.epic === e.key);
      const starts = its
        .map((i) => i.planned_start)
        .filter(Boolean)
        .sort();
      const ends = its
        .map((i) => i.planned_end)
        .filter(Boolean)
        .sort();
      if (!starts.length || !ends.length) return [];
      const done = its.filter((i) => i.state === "done").length;
      return [
        {
          key: e.key,
          title: `${e.key} ${e.title}`,
          type: "epic",
          planned_start: starts[0],
          planned_end: ends.at(-1),
          progress_pct: Math.round((done / its.length) * 100),
          rollup: true,
          ...ghost(
            its
              .map((i) => i.baseline_end)
              .filter(Boolean)
              .sort()
              .at(-1),
          ),
        },
      ];
    });
    return { rows: out, deps: [] };
  }
  if (rows === "milestone") {
    const out = D.milestones.filter(inProject).map((m) => {
      const starts = [
        ...D.deliverables
          .filter((d) => d.project === m.project)
          .map((d) => d.planned_start),
        ...D.phases
          .filter((p) => p.project === m.project)
          .map((p) => p.planned_start),
      ]
        .filter(Boolean)
        .sort();
      return {
        key: `${m.project}:${m.name}`,
        title: m.name,
        type: "milestone",
        status: m.status,
        planned_start: starts[0] ?? m.due,
        planned_end: m.due,
        progress_pct: m.deliverables_total
          ? Math.round((m.deliverables_done / m.deliverables_total) * 100)
          : null,
      };
    });
    return { rows: out, deps: [] };
  }
  const phases = D.phases.filter(inProject);
  const delivs = D.deliverables.filter((d) =>
    phases.some((p) => p.key === d.phase),
  );
  const out = phases.flatMap((p) => [
    { type: "phase", ...p },
    ...delivs
      .filter((d) => d.phase === p.key)
      .map((d) => ({ type: "deliverable", depth: 1, ...d })),
  ]);
  for (const r of out)
    if (r.baseline_end && !r.baseline)
      r.baseline = { planned_end: r.baseline_end };
  const deps = delivs.flatMap((d) =>
    (d.depends_on ?? []).map((k) => ({ from: k, to: d.key })),
  );
  return { rows: out, deps };
}
