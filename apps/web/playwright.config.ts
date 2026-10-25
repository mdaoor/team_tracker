import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(process.cwd(), "../..");
const api = "http://localhost:8010";

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  workers: 1,
  use: {
    // TT_VIDEO=1 records every test (the acceptance walkthrough in CI); off otherwise
    video: process.env.TT_VIDEO ? { mode: "on", size: { width: 1280, height: 720 } } : "off",
    // CI images may ship a different Chromium build than this Playwright expects: TT_CHROMIUM points at it.
    launchOptions: { executablePath: process.env.TT_CHROMIUM || undefined },
    baseURL: process.env.TT_WEB_URL ?? "http://localhost:5173",
    // dev auth: the API trusts this header only when TT_DEV_AUTH=1 (never in production)
    extraHTTPHeaders: { "X-Debug-Actor": "@mahmoud" },
  },
  webServer: process.env.TT_WEB_URL
    ? undefined
    : [
        {
          command: `${root}/.venv/bin/uvicorn tt_server.main:create_app --factory --port 8010`,
          url: `${api}/healthz`,
          reuseExistingServer: false,
          env: {
            TT_DATABASE_URL: "postgresql+psycopg://tt:tt@localhost/tt_e2e",
            TT_DEV_AUTH: "1",
            TT_LOG_JSON: "false",
          },
        },
        {
          command: "pnpm dev --port 5173",
          url: "http://localhost:5173",
          reuseExistingServer: true,
          env: { TT_API_URL: api },
        },
      ],
});
