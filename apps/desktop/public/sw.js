/* Service worker of the installable phone app ("Mobile Lite", ADR-0057). It caches the app shell so the planner opens with no network.
 *
 *  - install: fetch ./mobile.html, read the script/style/icon URLs out of it (their names carry a content hash, so they cannot be listed
 *    here) and cache all of it. A page that was installed once opens offline.
 *  - fetch: navigations go to the network first and fall back to the cached shell; other same-origin GETs are cache-first and filled on
 *    first use; anything under /api/ and any other origin is never touched (data is not cached by the worker: it lives in the page's
 *    localStorage, and sends are queued there).
 *  - activate: old caches of this app are removed.
 * The page never depends on this file: without it the app works online only. */
const VERSION = "tt-mobile-v1";
const SHELL_PAGE = "./mobile.html";
const STATIC = ["./manifest.webmanifest", "./icons/icon-192.png", "./icons/icon-512.png"];

/** URLs referenced by an HTML document (src/href), resolved against `base`, same origin only. */
function assetsOf(html, base) {
  const out = new Set();
  for (const m of html.matchAll(/(?:src|href)\s*=\s*["']([^"']+)["']/gi)) {
    let u;
    try {
      u = new URL(m[1], base);
    } catch (e) {
      continue;
    }
    if (u.origin === new URL(base).origin && !u.pathname.endsWith("/")) out.add(u.href);
  }
  return [...out];
}

async function precache() {
  const cache = await caches.open(VERSION);
  const page = new URL(SHELL_PAGE, self.registration.scope).href;
  const res = await fetch(page, { cache: "reload" });
  if (!res.ok) throw new Error("shell page not available: " + res.status);
  const html = await res.clone().text();
  await cache.put(page, res);
  const urls = [...new Set([...assetsOf(html, page), ...STATIC.map((u) => new URL(u, self.registration.scope).href)])];
  await Promise.all(
    urls.map(async (u) => {
      try {
        const r = await fetch(u, { cache: "reload" });
        if (r.ok) await cache.put(u, r);
      } catch (e) {
        /* one asset failing must not block the install of the rest */
      }
    }),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("tt-mobile-") && k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== new URL(self.registration.scope).origin) return;
  if (url.pathname.includes("/api/")) return;
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((c) => c.put(url.pathname.endsWith("mobile.html") ? url.href : new URL(SHELL_PAGE, self.registration.scope).href, copy));
          }
          return res;
        })
        .catch(() => caches.match(new URL(SHELL_PAGE, self.registration.scope).href)),
    );
    return;
  }
  event.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((c) => c.put(req, copy));
          }
          return res;
        }),
    ),
  );
});
