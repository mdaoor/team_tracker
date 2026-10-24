import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  workers: 1,
  use: {
    baseURL: "http://localhost:4173",
    launchOptions: { executablePath: process.env.TT_CHROMIUM || undefined },
    viewport: { width: 1280, height: 720 },
  },
  webServer: {
    command: "node e2e/serve.mjs",
    url: "http://localhost:4173/",
    reuseExistingServer: true,
  },
});
