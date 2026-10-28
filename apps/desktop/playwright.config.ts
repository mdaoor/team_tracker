import { defineConfig } from "@playwright/test";

/** Real-browser audit of the personal tracker views (Phase 15): axe with colour contrast in light and dark, no horizontal scroll at 390 px,
 * and a keyboard-only run. It drives `demo.html` (in-memory data, fixed clock), so no sidecar or server is needed.
 * CI images may ship a different Chromium than this Playwright expects: TT_CHROMIUM points at the executable. */
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  use: {
    baseURL: process.env.TT_DESKTOP_URL ?? "http://localhost:1420",
    launchOptions: { executablePath: process.env.TT_CHROMIUM || undefined },
  },
  webServer: process.env.TT_DESKTOP_URL
    ? undefined
    : {
        command: "pnpm dev --port 1420",
        url: "http://localhost:1420/demo.html",
        reuseExistingServer: true,
      },
});
