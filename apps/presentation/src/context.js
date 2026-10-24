import { applyFilter, scopeItems } from "./util.js";
import { habitList } from "./renderers/people.js";
import { peopleList } from "./renderers/people.js";
import { radarRows } from "./renderers/time.js";

/** Everything a renderer reads. `items` is the scoped list (page mode narrows it as the viewer drills). */
export function makeContext(data, { interactive = false } = {}) {
  const D = data.snapshot;
  // snapshots from before the plan fields (golden fixtures, older exports) simply have no plan
  D.phases ??= [];
  D.deliverables ??= [];
  const presentation = data.presentation;
  const tables = new Set();
  const ctx = {
    D,
    presentation,
    slideData: data.slide_data ?? {},
    items: D.items,
    interactive,
    stamp:
      window.__DECK_STAMP__ ||
      `${D.meta.viewer} · ${D.meta.scope_label} · as of ${D.as_of.replace("T", " ").slice(0, 16)}`,
    tableMode: (id) => tables.has(id),
    toggleTable: (id) => (tables.has(id) ? tables.delete(id) : tables.add(id)),
    restricted: (id) => !!(data.slide_data ?? {})[id]?.restricted,
    radarRows: (cfg) => radarRows(ctx, cfg),
    peopleCount: () => peopleList(ctx).length,
    projectCount: () =>
      D.projects.filter((p) => ctx.items.some((i) => i.project === p.key))
        .length,
    habitCount: () => habitList(ctx).length,
    scope(scope, path = []) {
      let items = scopeItems(D, scope);
      for (const f of path) items = applyFilter(items, f.f, D);
      ctx.items = items;
      return items;
    },
  };
  return ctx;
}
