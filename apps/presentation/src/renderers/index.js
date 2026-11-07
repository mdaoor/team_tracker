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
import {
  planEmpty,
  renderDeliverables,
  renderMilestones,
  renderPhaseStatus,
  renderPlanVsActual,
  renderProjectOverview,
  renderRoadmap,
} from "./plan.js";
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
  roadmap: renderRoadmap,
  milestones: renderMilestones,
  deliverables: renderDeliverables,
  plan_vs_actual: renderPlanVsActual,
  phase_status: renderPhaseStatus,
  project_overview: renderProjectOverview,
};

/** What a kind needs to say anything (the plan-only rule): plan kinds draw from manager-entered data, activity kinds from reported
 *  work and say so when there is none. Mirrored in `tt_server/domain/presentations.py` (a test keeps the two equal). */
export const KIND_META = {
  title: { requires: "plan", render_empty: "placeholder" }, section_divider: { requires: "plan", render_empty: "placeholder" },
  roadmap: { requires: "plan", render_empty: "placeholder" }, milestones: { requires: "plan", render_empty: "placeholder" },
  deliverables: { requires: "plan", render_empty: "placeholder" }, plan_vs_actual: { requires: "plan", render_empty: "placeholder" },
  phase_status: { requires: "plan", render_empty: "placeholder" }, project_overview: { requires: "plan", render_empty: "placeholder" },
  projects: { requires: "both", render_empty: "placeholder" }, project_detail: { requires: "both", render_empty: "placeholder" },
  gantt: { requires: "both", render_empty: "placeholder" }, insight: { requires: "both", render_empty: "note" },
  explorer: { requires: "activity", render_empty: "note" }, radar: { requires: "activity", render_empty: "note" },
  kpis: { requires: "activity", render_empty: "note" }, pipeline: { requires: "activity", render_empty: "note" },
  now: { requires: "activity", render_empty: "note" }, people: { requires: "activity", render_empty: "note" },
  violations: { requires: "activity", render_empty: "note" }, habits: { requires: "activity", render_empty: "note" },
  view: { requires: "activity", render_empty: "note" }, chart: { requires: "activity", render_empty: "note" },
  table: { requires: "activity", render_empty: "note" }, markdown: { requires: "plan", render_empty: "placeholder" },
  image: { requires: "plan", render_empty: "placeholder" },
}; // prettier-ignore
export { planEmpty };
export const TITLES = { kpis: "Overview", projects: "Projects & epics", pipeline: "Delivery pipeline", explorer: "Dimension explorer", radar: "Delivery radar", gantt: "Gantt", now: "Now", people: "Team activity", violations: "Violations & habits", habits: "Habits", insight: "Insights", view: "View", chart: "Chart", table: "Table", markdown: "Notes", image: "Image", title: "Title", section_divider: "Section", project_detail: "Project", roadmap: "Roadmap", milestones: "Milestones", deliverables: "Deliverables", plan_vs_actual: "Plan vs actual", phase_status: "Phase status", project_overview: "Project overview" }; // prettier-ignore
