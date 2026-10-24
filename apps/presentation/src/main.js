import { makeContext } from "./context.js";
import { mountDeck } from "./deck.js";
import { mountPage } from "./page.js";

async function load() {
  if (window.__DECK__) return window.__DECK__;
  const q = new URLSearchParams(location.search);
  const src = q.get("src") || window.__DECK_SRC__;
  if (!src)
    throw new Error("no data: open /p/<workspace>/<id>, or add ?src=file.json");
  const r = await fetch(src, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!r.ok)
    throw new Error(
      r.status === 404
        ? "This presentation does not exist, or you may not see it."
        : `Could not load the presentation (${r.status}).`,
    );
  return r.json();
}

function theme(pref) {
  const root = document.documentElement;
  if (pref === "dark" || pref === "light") root.dataset.theme = pref;
  document.addEventListener(
    "tt-theme",
    () =>
      (root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark"),
  );
}

async function boot() {
  const app = document.getElementById("app");
  try {
    const data = await load();
    const q = new URLSearchParams(location.search);
    const mode = q.get("mode") || window.__DECK_MODE__ || "deck";
    theme(q.get("theme") || data.presentation.theme);
    document.title = `${data.presentation.title} — Team Tracker`;
    const ctx = makeContext(data, { interactive: mode === "page" });
    const handle =
      mode === "page"
        ? mountPage(app, ctx)
        : mountDeck(app, ctx, { print: mode === "print" });
    document.documentElement.dataset.mode = mode;
    window.__deck = { ...handle, mode, ctx };
    document.documentElement.dataset.ready = "1";
  } catch (err) {
    app.innerHTML = `<div class="fatal" role="alert"><h1>Presentation unavailable</h1><p>${String(err.message).replace(/[<>&]/g, "")}</p></div>`;
    document.documentElement.dataset.ready = "error";
  }
}
boot();
