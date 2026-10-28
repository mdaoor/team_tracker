import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(process.cwd(), "../..");
// ports and database are overridable so parallel runs (other agents, CI) do not collide
const apiPort = process.env.TT_E2E_API_PORT ?? "8010";
const webPort = process.env.TT_E2E_WEB_PORT ?? "5173";
const dbName = process.env.TT_E2E_DB ?? "tt_e2e";
const api = `http://localhost:${apiPort}`;

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  workers: 1,
  use: {
    // TT_VIDEO=1 records every test (the acceptance walkthrough in CI); off otherwise
    video: process.env.TT_VIDEO ? { mode: "on", size: { width: 1280, height: 720 } } : "off",
    // CI images may ship a different Chromium build than this Playwright expects: TT_CHROMIUM points at it.
    launchOptions: { executablePath: process.env.TT_CHROMIUM || undefined },
    baseURL: process.env.TT_WEB_URL ?? `http://localhost:${webPort}`,
    // dev auth: the API trusts this header only when TT_DEV_AUTH=1 (never in production)
    extraHTTPHeaders: { "X-Debug-Actor": "@mahmoud" },
  },
  webServer: process.env.TT_WEB_URL
    ? undefined
    : [
        {
          command: `${root}/.venv/bin/uvicorn tt_server.main:create_app --factory --port ${apiPort}`,
          url: `${api}/healthz`,
          reuseExistingServer: false,
          env: {
            TT_DATABASE_URL: `postgresql+psycopg://tt:tt@localhost/${dbName}`,
            TT_DEV_AUTH: "1",
            TT_LOG_JSON: "false",
          },
        },
        {
          command: `pnpm dev --port ${webPort} --strictPort`,
          url: `http://localhost:${webPort}`,
          reuseExistingServer: !process.env.TT_E2E_WEB_PORT,
          env: { TT_API_URL: api },
        },
      ],
});
