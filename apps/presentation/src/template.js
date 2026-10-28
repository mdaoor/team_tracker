import { KIND_META, RENDERERS, TITLES } from "./renderers/index.js";
import { esc } from "./util.js";

const ROWS = {
  radar: { per: 24, count: (ctx, cfg) => ctx.radarRows(cfg) },
  people: { per: 12, count: (ctx) => ctx.peopleCount() },
  table: {
    per: 12,
    count: (ctx, cfg, s) => (ctx.slideData[s.id]?.items ?? []).length,
  },
  view: {
    per: 12,
    count: (ctx, cfg, s) => (ctx.slideData[s.id]?.items ?? []).length,
  },
  projects: { per: 4, count: (ctx) => ctx.projectCount() },
  deliverables: {
    per: 10,
    count: (ctx, cfg) =>
      ctx.D.deliverables.filter(
        (d) =>
          (!cfg.project_key || d.project === cfg.project_key) &&
          (!cfg.phase || d.phase === cfg.phase),
      ).length,
  },
  plan_vs_actual: {
    per: 12,
    count: (ctx, cfg) =>
      ctx.D.deliverables.filter(
        (d) => !cfg.project_key || d.project === cfg.project_key,
      ).length,
  },
};

/** Long tables paginate into continuation slides ("(cont.)") instead of overflowing. */
export function expandSlides(slides, ctx) {
  const out = [];
  for (const s of slides) {
    const r = ROWS[s.kind];
    const n = r ? r.count(ctx, s.config, s) : 0;
    if (!r || n <= r.per) {
      out.push(s);
      continue;
    }
    if (s.fit === "shrink") {
      // one slide, scaled so `per` rows' worth of height holds all `n` rows (never below half size)
      out.push({ ...s, _shrink: Math.max(0.5, r.per / n) });
      continue;
    }
    const pages = Math.ceil(n / r.per);
    for (let k = 0; k < pages; k++)
      out.push({
        ...s,
        _rows: [k * r.per, (k + 1) * r.per],
        _cont: k > 0,
        _origin: s.id,
        id: k ? `${s.id}~${k}` : s.id,
      });
  }
  return out;
}

export function slideBody(ctx, slide) {
  const fn = RENDERERS[slide.kind];
  if (!fn)
    return `<div class="card">Unknown slide kind ${esc(slide.kind)}</div>`;
  const meta = KIND_META[slide.kind];
  if (
    meta?.requires === "activity" &&
    !(ctx.D.items ?? []).length &&
    !ctx.slideData?.[slide.id]?.items?.length
  )
    return `<div class="card empty">No work reported yet for this scope.</div>`;
  const slideCtx = slide._origin
    ? { ...ctx, tableMode: (id) => ctx.tableMode(slide._origin) }
    : ctx;
  return fn(slideCtx, slide.config ?? {}, slide);
}

export function slideTitle(slide) {
  return (
    (slide.title || TITLES[slide.kind] || slide.kind) +
    (slide._cont ? " (cont.)" : "")
  );
}

/** header (title, subtitle, scope chip, as-of) + body (renderer output) + footer (stamp, page n/N). */
export function slideHtml(ctx, slide, n, total) {
  const D = ctx.D;
  const bare = slide.kind === "title" || slide.kind === "section_divider";
  return `<article class="slide ${bare ? "bare" : ""}" data-kind="${esc(slide.kind)}" data-id="${esc(slide.id)}" data-layout="${esc(slide.layout ?? "full")}" aria-label="${esc(slideTitle(slide))}">
    ${bare ? "" : `<header class="s-head"><div><h2 class="s-title">${esc(slideTitle(slide))}</h2>${slide.subtitle ? `<div class="s-sub">${esc(slide.subtitle)}</div>` : ""}</div><div class="s-meta"><span class="chip scope">${esc(D.meta.scope_label)}</span><span>as of ${esc(D.as_of.replace("T", " ").slice(0, 16))}</span></div></header>`}
    <div class="s-body"${slide._shrink ? ` data-shrink="1" style="--fit:${slide._shrink.toFixed(3)}"` : ""}>${slideBody(ctx, slide)}</div>
    <footer class="s-foot"><span class="stamp">${esc(ctx.stamp)}</span><span class="pageno">${n} / ${total}</span></footer>
  </article>`;
}
