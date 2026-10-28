/** Register the shell-caching service worker. Production builds only (in `vite dev` a cache would hide edits), and never an error for the
 * person: without a worker the page still works online, it just does not open offline. The path is relative so the app can live under any
 * prefix (`TT_BASE=/m/ pnpm build`). */
export function registerServiceWorker(
  nav: Pick<Navigator, "serviceWorker"> | undefined = typeof navigator ===
  "undefined"
    ? undefined
    : navigator,
  prod: boolean = import.meta.env.PROD,
): Promise<boolean> {
  if (!prod || !nav || !("serviceWorker" in nav) || !nav.serviceWorker)
    return Promise.resolve(false);
  return nav.serviceWorker
    .register("./sw.js")
    .then(() => true)
    .catch(() => false);
}
