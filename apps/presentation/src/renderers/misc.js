import { esc } from "../util.js";

export function md(text) {
  const lines = String(text ?? "").split(/\n/);
  const out = [];
  let list = false;
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
  for (const ln of lines) {
    const li = /^\s*[-*]\s+(.*)/.exec(ln);
    if (li) {
      if (!list) (out.push("<ul>"), (list = true));
      out.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    if (list) (out.push("</ul>"), (list = false));
    const h = /^(#{1,3})\s+(.*)/.exec(ln);
    if (h)
      out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`);
    else if (ln.trim()) out.push(`<p>${inline(ln)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}
export const renderMarkdown = (ctx, cfg) =>
  `<div class="md">${md(cfg.text)}</div>`;

export function renderImage(ctx, cfg) {
  const src = cfg.url || "";
  return src && /^(data:image\/(png|jpeg|gif|webp);|\/(?!\/))/.test(src)
    ? `<div class="img"><img alt="${esc(cfg.alt ?? "")}" src="${esc(src)}"></div>`
    : `<div class="card">No image selected.</div>`;
}
export const renderTitle = (ctx, cfg, slide) =>
  `<div class="title-slide"><h1>${esc(slide.title ?? ctx.presentation.title)}</h1><p>${esc(slide.subtitle ?? ctx.D.meta.scope_label)}</p><p class="s-note">${esc(ctx.D.meta.scope_label)} · ${esc(ctx.D.range.from)} → ${esc(ctx.D.range.to)} · weights: ${esc(ctx.D.weight_scheme)}</p></div>`;
export const renderDivider = (ctx, cfg, slide) =>
  `<div class="title-slide divider"><h1>${esc(slide.title ?? "")}</h1></div>`;

export function renderInsight(ctx, cfg, slide) {
  const d = ctx.slideData[slide.id];
  const keys = new Set(ctx.items.map((i) => i.project));
  const only = cfg.scope || cfg.project_key;
  const stored = (d?.insights ?? []).filter(
    (x) => keys.has(x.project) && (!only || x.project === only),
  );
  const seen = new Set();
  const cards = [];
  for (const x of stored) {
    if (seen.has(x.project)) continue; // newest first
    seen.add(x.project);
    cards.push(
      `<div class="card"><h3>${esc(x.project)} <span class="k">${esc(x.status)}</span></h3><p>${esc(x.body)}</p><div class="meta">${(x.supporting_ids ?? []).map((k) => `<a class="pill" href="#" data-open="${esc(k)}">${esc(k)}</a>`).join(" ")}</div></div>`,
    );
  }
  for (const p of ctx.D.projects) {
    if (seen.has(p.key) || !keys.has(p.key) || (only && p.key !== only))
      continue;
    cards.push(
      `<div class="card"><h3>${esc(p.name)} <span class="k">${esc(p.key)}</span></h3><p>${esc(p.summary)}</p></div>`,
    );
  }
  return `<div class="grid2">${cards.join("") || `<div class="card">No summaries in scope.</div>`}</div>`;
}
