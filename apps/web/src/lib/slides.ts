import type { Json } from "../api/http";
import { sortParam, toFilter, type ViewState } from "./viewState";

export interface SlideDraft {
  kind: string;
  title?: string;
  config: Json;
  live?: boolean;
}

/**
 * "Add to presentation": freeze what the viewer is looking at into a slide. A matrix becomes a `chart` slide (rows x cols x
 * measure over the current filter), every other layout a `table` slide. `live` makes the deck re-run the query when presented.
 */
export function slideFromView(st: ViewState): SlideDraft {
  const filter = toFilter(st.chips);
  if (st.layout === "matrix") {
    const { rows, cols, measure } = st.matrix;
    return {
      kind: "chart",
      title: `${rows} × ${cols}`,
      config: { rows, cols, measure, filter },
      live: true,
    };
  }
  return {
    kind: "table",
    title: st.chips.length ? st.chips.map((c) => c.label).join(" · ") : "All items",
    config: { filter, sort: sortParam(st.sort), limit: 50 },
    live: true,
  };
}

export const SLIDE_LABELS: Record<string, string> = {
  title: "Title", kpis: "Key numbers", projects: "Projects", project_detail: "Project detail", pipeline: "Pipeline",
  explorer: "Explorer", radar: "Radar", gantt: "Gantt", now: "Now", people: "People", violations: "Violations",
  habits: "Habits", insight: "Insight", view: "Saved view", chart: "Chart", table: "Table", markdown: "Notes",
  image: "Image", section_divider: "Section divider", roadmap: "Roadmap", milestones: "Milestones", deliverables: "Deliverables",
  plan_vs_actual: "Plan vs actual", phase_status: "Phase status", project_overview: "Project overview",
}; // prettier-ignore

export const SLIDE_ICONS: Record<string, string> = {
  title: "▤", kpis: "#", projects: "▦", project_detail: "▣", pipeline: "⇉", explorer: "▥", radar: "◎", gantt: "▬",
  now: "●", people: "☺", violations: "⚠", habits: "∿", insight: "✦", view: "◫", chart: "▮", table: "☰", markdown: "¶",
  image: "▨", section_divider: "—", roadmap: "⟿", milestones: "◆", deliverables: "☑", plan_vs_actual: "⇄", phase_status: "▭",
  project_overview: "⌂",
}; // prettier-ignore
