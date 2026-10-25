import { applyFilter, esc } from "./util.js";
import { itemsTable } from "./renderers/explore.js";

/**
 * Click / keyboard handling shared by deck and page: `[data-open]` opens the item drawer, `[data-toggle]` flips a chart/table,
 * `[data-drill]` is handed to `onDrill`. Rows and tiles are focusable, Enter/Space activate them.
 */
export function wireInteractions(root, { ctx, drawer, onDrill, rerender }) {
  const act = (el, e) => {
    const open = el.closest("[data-open]");
    if (open) {
      e.preventDefault();
      drawer.open(open.dataset.open);
      return true;
    }
    const tg = el.closest("[data-toggle]");
    if (tg) {
      e.preventDefault();
      ctx.toggleTable(tg.dataset.toggle.split("~")[0]);
      rerender();
      return true;
    }
    const dr = el.closest("[data-drill]");
    if (dr) {
      e.preventDefault();
      onDrill(JSON.parse(dr.dataset.drill));
      return true;
    }
    return false;
  };
  root.addEventListener("click", (e) => act(e.target, e));
  root.addEventListener("keydown", (e) => {
    if (
      (e.key === "Enter" || e.key === " ") &&
      e.target.matches?.("[data-open],[data-drill],tr.row,.kpi,.col")
    ) {
      if (act(e.target, e)) e.stopPropagation();
    }
  });
  root.addEventListener("change", (e) => {
    const c = e.target.closest?.("[data-ctl]");
    if (c && ctx.interactive) rerender({ ctl: c.dataset.ctl, value: c.value });
  });
}

export function drillList(ctx, spec) {
  const items = applyFilter(ctx.items, spec.f, ctx.D);
  return {
    items,
    html: `<h2>Items in selection <span class="sub">${items.length} · ${esc(spec.label)}</span></h2>${spec.note ? `<p class="drill-note">${esc(spec.note)}</p>` : ""}${itemsTable(ctx, items)}`,
  };
}
