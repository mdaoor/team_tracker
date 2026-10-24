import { renderChart, renderExplorer, renderQueryItems } from "./explore.js";
import {
  renderDivider,
  renderImage,
  renderInsight,
  renderMarkdown,
  renderTitle,
} from "./misc.js";
import { renderKpis, renderPipeline, renderProjects } from "./overview.js";
import {
  renderHabits,
  renderPeople,
  renderViolations,
  renderViolHabits,
} from "./people.js";
import { renderGantt, renderNow, renderRadar } from "./time.js";

/** `render<Kind>(ctx, config, slide) -> html`: the same function draws a slide, a page section or a view embed. */
export const RENDERERS = {
  title: renderTitle,
  section_divider: renderDivider,
  kpis: renderKpis,
  projects: renderProjects,
  project_detail: (ctx, cfg, slide) =>
    renderProjects(ctx, { project_key: cfg.project_key }, slide),
  pipeline: renderPipeline,
  explorer: renderExplorer,
  radar: renderRadar,
  gantt: renderGantt,
  now: renderNow,
  people: renderPeople,
  violations: (ctx, cfg, slide) =>
    cfg.with_habits
      ? renderViolHabits(ctx, cfg, slide)
      : renderViolations(ctx, cfg, slide),
  habits: renderHabits,
  insight: renderInsight,
  view: renderQueryItems,
  table: renderQueryItems,
  chart: renderChart,
  markdown: renderMarkdown,
  image: renderImage,
};
export const TITLES = { kpis: "Overview", projects: "Projects & epics", pipeline: "Delivery pipeline", explorer: "Dimension explorer", radar: "Delivery radar", gantt: "Gantt", now: "Now", people: "Team activity", violations: "Violations & habits", habits: "Habits", insight: "Insights", view: "View", chart: "Chart", table: "Table", markdown: "Notes", image: "Image", title: "Title", section_divider: "Section", project_detail: "Project" }; // prettier-ignore
