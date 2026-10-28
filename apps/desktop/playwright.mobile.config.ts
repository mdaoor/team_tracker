import { defineConfig } from "@playwright/test";

/** Mobile Lite (ADR-0057) against the PRODUCTION build: a service worker only exists there. `vite build` then `vite preview` on :4173.
 * TT_CHROMIUM points at a Chromium executable when the installed one differs from Playwright's expected build. */
export default defineConfig({
  testDir: "e2e",
  testMatch: /mobile\.spec\.ts/,
  workers: 1,
  use: {
    baseURL: process.env.TT_MOBILE_URL ?? "http://localhost:4173",
    launchOptions: { executablePath: process.env.TT_CHROMIUM || undefined },
    serviceWorkers: "allow",
  },
  webServer: process.env.TT_MOBILE_URL
    ? undefined
    : {
        command: "pnpm exec vite build && pnpm exec vite preview --port 4173 --strictPort",
        url: "http://localhost:4173/mobile.html",
        reuseExistingServer: true,
        timeout: 120000,
      },
});
