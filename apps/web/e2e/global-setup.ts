import { execFileSync } from "node:child_process";
import path from "node:path";

/** Fresh seeded database for the run: the dev-auth API in playwright.config.ts points at it. */
export default function globalSetup() {
  const root = path.resolve(process.cwd(), "../..");
  const bin = (n: string) => path.join(root, ".venv", "bin", n);
  const admin = process.env.TT_E2E_ADMIN_URL ?? "postgresql://tt:tt@localhost/postgres";
  const sql = (q: string) =>
    execFileSync("psql", [admin, "-v", "ON_ERROR_STOP=1", "-c", q], { stdio: "ignore" });
  const db = process.env.TT_E2E_DB ?? "tt_e2e";
  sql(`DROP DATABASE IF EXISTS ${db} WITH (FORCE)`);
  sql(`CREATE DATABASE ${db}`);
  const env = { ...process.env, TT_DATABASE_URL: `postgresql+psycopg://tt:tt@localhost/${db}` };
  execFileSync(bin("tt-admin"), ["migrate"], { env, stdio: "ignore" });
  execFileSync(bin("tt-admin"), ["seed", "demo"], { env, stdio: "ignore" });
}
