import { drillList, wireInteractions } from "./interact.js";
import { mountDrawer } from "./drawer.js";
import { expandSlides, slideHtml } from "./template.js";

const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

/** Deck mode: one slide per screen. Keys: → ← Space PgUp PgDn Home End, F fullscreen, N notes, G grid, T theme, Esc. */
export function mountDeck(root, ctx, { print = false } = {}) {
  const slides = expandSlides(ctx.presentation.slides, ctx);
  const total = slides.length;
  root.innerHTML = `<div class="deck ${print ? "print" : ""}" tabindex="-1">
    <div class="progress" role="progressbar" aria-label="Slide progress" aria-valuemin="1" aria-valuemax="${total}"><i></i></div>
    <main class="viewport" aria-label="Slide"></main>
    <nav class="deck-nav"><button class="ghost" data-nav="prev" aria-label="Previous slide">←</button><span class="counter" aria-live="polite"></span><button class="ghost" data-nav="next" aria-label="Next slide">→</button><button class="ghost" data-nav="grid" aria-label="Slide overview" title="Overview (G)">▦</button><button class="ghost" data-nav="notes" aria-label="Speaker notes" title="Notes (N)">✎</button><button class="ghost" data-nav="full" aria-label="Fullscreen" title="Fullscreen (F)">⛶</button></nav>
    <aside class="notes" hidden></aside>
    <div class="grid-overview" hidden role="dialog" aria-label="Slide overview"></div>
    <div class="overlay" hidden role="dialog" aria-label="Drill-down"><button class="close ghost" aria-label="Close">✕</button><div class="overlay-body"></div></div>
  </div>`;
  const el = {
    deck: root.querySelector(".deck"),
    vp: root.querySelector(".viewport"),
    counter: root.querySelector(".counter"),
    bar: root.querySelector(".progress i"),
    progress: root.querySelector(".progress"),
    notes: root.querySelector(".notes"),
    grid: root.querySelector(".grid-overview"),
    overlay: root.querySelector(".overlay"),
  };
  const drawer = mountDrawer(el.deck, ctx.D);
  const rendered = new Map();
  let idx = 0;

  const html = (i) => {
    if (!rendered.has(i))
      rendered.set(i, slideHtml(ctx, slides[i], i + 1, total));
    return rendered.get(i);
  };

  function fit() {
    const k = Math.min(
      window.innerWidth / 1280,
      (window.innerHeight - 0) / 720,
    );
    el.deck.style.setProperty("--k", String(k));
  }

  function show(i, { push = true } = {}) {
    idx = clamp(i, 0, total - 1);
    el.vp.innerHTML = html(idx);
    el.vp.firstElementChild.classList.add("active");
    el.counter.textContent = `${idx + 1} / ${total}`;
    el.bar.style.width = `${((idx + 1) / total) * 100}%`;
    el.progress.setAttribute("aria-valuenow", String(idx + 1));
    el.notes.textContent = slides[idx].notes || "No notes for this slide.";
    if (push) {
      const u = new URL(location.href);
      u.searchParams.set("slide", String(idx + 1));
      history.replaceState(null, "", u);
    }
  }
  const rerender = () => {
    rendered.clear();
    show(idx, { push: false });
  };

  const closeOverlays = () => {
    if (drawer.isOpen()) return (drawer.close(), true);
    if (!el.overlay.hidden) return ((el.overlay.hidden = true), true);
    if (!el.grid.hidden) return ((el.grid.hidden = true), true);
    if (!el.notes.hidden) return ((el.notes.hidden = true), true);
    return false;
  };
  const toggleGrid = () => {
    el.grid.hidden = !el.grid.hidden;
    if (!el.grid.hidden) {
      el.grid.innerHTML = slides
        .map(
          (s, i) =>
            `<button class="thumb ${i === idx ? "current" : ""}" data-go="${i}" aria-label="Slide ${i + 1}: ${(s.title || s.kind).replace(/"/g, "")}"><div class="thumb-inner">${html(i).replace('class="slide', 'class="slide static')}</div><span>${i + 1}</span></button>`,
        )
        .join("");
      el.grid.querySelector(".current")?.focus();
    }
  };
  el.grid.addEventListener("click", (e) => {
    const b = e.target.closest("[data-go]");
    if (b) {
      el.grid.hidden = true;
      show(+b.dataset.go);
    }
  });
  const fullscreen = () =>
    document.fullscreenElement
      ? document.exitFullscreen()
      : el.deck.requestFullscreen?.();

  wireInteractions(el.vp, {
    ctx,
    drawer,
    rerender,
    onDrill(spec) {
      const { html: h } = drillList(ctx, spec);
      el.overlay.querySelector(".overlay-body").innerHTML = h;
      el.overlay.hidden = false;
      el.overlay.querySelector(".close").focus();
    },
  });
  wireInteractions(el.overlay, { ctx, drawer, rerender, onDrill() {} });
  el.overlay.querySelector(".close").onclick = () => (el.overlay.hidden = true);

  root.querySelector(".deck-nav").addEventListener("click", (e) => {
    const n = e.target.closest("[data-nav]")?.dataset.nav;
    if (n === "prev") show(idx - 1);
    if (n === "next") show(idx + 1);
    if (n === "grid") toggleGrid();
    if (n === "notes") el.notes.hidden = !el.notes.hidden;
    if (n === "full") fullscreen();
  });

  document.addEventListener("keydown", (e) => {
    if (
      e.metaKey ||
      e.ctrlKey ||
      e.altKey ||
      /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)
    )
      return;
    const typing = e.target.closest?.(".overlay, .drawer, .grid-overview");
    switch (e.key) {
      case "ArrowRight":
      case "PageDown":
        if (!typing || e.key === "ArrowRight")
          return (e.preventDefault(), show(idx + 1));
        break;
      case " ":
        if (
          e.target.matches?.("button,[data-open],[data-drill],tr.row,.kpi,.col")
        )
          return;
        return (e.preventDefault(), show(idx + 1));
      case "ArrowLeft":
      case "PageUp":
        return (e.preventDefault(), show(idx - 1));
      case "Home":
        return (e.preventDefault(), show(0));
      case "End":
        return (e.preventDefault(), show(total - 1));
      case "f":
      case "F":
        return fullscreen();
      case "n":
      case "N":
        el.notes.hidden = !el.notes.hidden;
        return;
      case "g":
      case "G":
        return toggleGrid();
      case "t":
      case "T":
        return document.dispatchEvent(new CustomEvent("tt-theme"));
      case "Escape":
        if (closeOverlays()) e.preventDefault();
        return;
    }
  });

  let x0 = null;
  el.vp.addEventListener("touchstart", (e) => (x0 = e.touches[0].clientX), {
    passive: true,
  });
  el.vp.addEventListener("touchend", (e) => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0;
    if (Math.abs(dx) > 50) show(idx + (dx < 0 ? 1 : -1));
    x0 = null;
  });
  window.addEventListener("resize", fit);
  fit();

  if (print) {
    el.deck.classList.add("print");
    el.vp.innerHTML = slides
      .map((s, i) => slideHtml(ctx, s, i + 1, total))
      .join("");
  } else {
    const q = parseInt(
      new URLSearchParams(location.search).get("slide") ?? "1",
      10,
    );
    show(Number.isFinite(q) ? q - 1 : 0, { push: false });
  }
  return { count: total, go: (i) => show(i), get index() { return idx; }, slides }; // prettier-ignore
}
