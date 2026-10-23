import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: process.env.TT_WEB_URL ?? "http://localhost:5173" },
  webServer: process.env.TT_WEB_URL
    ? undefined
    : { command: "pnpm dev --port 5173", url: "http://localhost:5173", reuseExistingServer: true },
});
