import { mountDrawer } from "./drawer.js";
import { drillList, wireInteractions } from "./interact.js";
import { expandSlides as _unused, slideBody, slideTitle } from "./template.js";
import { esc } from "./util.js";

void _unused;
const SUBS = { kpis: "click any number to see the items behind it", projects: "summary per project, next milestone, top risk", pipeline: "generic stages across every project — click a column", radar: "planned to finish in the next 14 days", gantt: "epics (planned span of their items) and milestones", now: "active sessions", people: "per person in range; habits visible per access rules" }; // prettier-ignore

/** Page mode: the original scrolling layout — every slide of the presentation stacked as a section, drill-down breadcrumb kept. */
export function mountPage(root, ctx) {
  const D = ctx.D;
  const days = Math.round(
    (new Date(D.range.to) - new Date(D.range.from)) / 864e5,
  );
  const state = { scope: "workspace:" + D.workspace, path: [] };
  const opts = [[`workspace:${D.workspace}`, `Workspace: ${D.workspace}`], ...D.products.map((p) => [`product:${p.key}`, `Product: ${p.name}`]), ...D.projects.map((p) => [`project:${p.key}`, `Project: ${p.name}`]), ...D.teams.map((t) => [`team:${t}`, `Team: ${t}`]), ...D.people.map((p) => [`person:${p.handle}`, `Person: ${p.name}`])]; // prettier-ignore
  const sections = ctx.presentation.slides.filter(
    (s) => s.kind !== "title" && s.kind !== "section_divider",
  );
  // the prototype shows violations and habits side by side in one section
  const merged = sections.filter(
    (s) =>
      s.kind !== "habits" ||
      !sections.some((x) => x.kind === "violations" && x.config.with_habits),
  );
  root.innerHTML = `<div class="page">
    <header><h1>${esc(ctx.presentation.title)}</h1>
      <select id="scope" aria-label="Scope">${opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>
      <select id="range" aria-label="Range" disabled title="${esc(D.range.from)} → ${esc(D.range.to)} (set in the presentation)"><option>Last ${days} days</option></select>
      <select id="scheme" aria-label="Weight scheme" disabled title="Set in the presentation"><option>Weight: ${esc(D.weight_scheme)}</option></select>
      <button class="ghost" id="theme" title="Toggle theme">◐</button><button class="ghost" id="print">Print</button>
      <span class="asof">as of ${esc(D.as_of.replace("T", " ").slice(0, 16))}</span></header>
    <main><div class="crumbs" id="crumbs"></div><div id="drill"></div>${merged.map((s) => `<section data-slide="${esc(s.id)}"><h2>${esc(slideTitle(s))} ${SUBS[s.kind] ? `<span class="sub">${esc(SUBS[s.kind])}</span>` : ""}</h2><div class="sec-body"></div></section>`).join("")}</main>
    <div class="stampline">${esc(ctx.stamp)}</div></div>`;
  const drawer = mountDrawer(root, D);
  const sel = root.querySelector("#scope");
  const crumbs = root.querySelector("#crumbs");
  const drillEl = root.querySelector("#drill");

  function paint() {
    ctx.scope(state.scope, state.path);
    crumbs.innerHTML = [
      `<span class="chip ${state.path.length ? "" : "active"}" data-pop="0">${esc(sel.selectedOptions[0].text)}</span>`,
      ...state.path.flatMap((p, i) => [
        "›",
        `<span class="chip ${i === state.path.length - 1 ? "active" : ""}" data-pop="${i + 1}">${esc(p.label)}</span>`,
      ]),
    ].join(" ");
    drillEl.innerHTML = state.path.length
      ? `<section>${
          drillList(ctx, {
            f: {},
            label: state.path.at(-1).label,
            note: state.path.at(-1).note,
          }).html
        }</section>`
      : "";
    merged.forEach((s) => {
      const host = root.querySelector(
        `[data-slide="${CSS.escape(s.id)}"] .sec-body`,
      );
      host.innerHTML = slideBody(ctx, s);
    });
  }
  function rerender(ctl) {
    if (ctl) {
      const sl = merged.find((s) => s.kind === "explorer");
      if (sl) sl.config = { ...sl.config, [ctl.ctl]: ctl.value };
    }
    paint();
  }
  sel.onchange = () => ((state.scope = sel.value), (state.path = []), paint());
  crumbs.addEventListener("click", (e) => {
    const n = e.target.closest("[data-pop]")?.dataset.pop;
    if (n != null) ((state.path = state.path.slice(0, +n)), paint());
  });
  root.querySelector("#theme").onclick = () =>
    document.dispatchEvent(new CustomEvent("tt-theme"));
  root.querySelector("#print").onclick = () => window.print();
  ctx.interactive = true;
  wireInteractions(root.querySelector("main"), {
    ctx,
    drawer,
    rerender,
    onDrill(spec) {
      state.path.push(spec);
      paint();
      drillEl.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
    },
  });
  document.addEventListener(
    "keydown",
    (e) => e.key === "Escape" && drawer.close(),
  );
  paint();
  return { count: merged.length, go() {}, index: 0, slides: merged };
}
