import { execFileSync } from "node:child_process";
import path from "node:path";

/** Fresh seeded database for the run: the dev-auth API in playwright.config.ts points at it. */
export default function globalSetup() {
  const root = path.resolve(process.cwd(), "../..");
  const bin = (n: string) => path.join(root, ".venv", "bin", n);
  const admin = process.env.TT_E2E_ADMIN_URL ?? "postgresql://tt:tt@localhost/postgres";
  const sql = (q: string) =>
    execFileSync("psql", [admin, "-v", "ON_ERROR_STOP=1", "-c", q], { stdio: "ignore" });
  sql("DROP DATABASE IF EXISTS tt_e2e WITH (FORCE)");
  sql("CREATE DATABASE tt_e2e");
  const env = { ...process.env, TT_DATABASE_URL: "postgresql+psycopg://tt:tt@localhost/tt_e2e" };
  execFileSync(bin("tt-admin"), ["migrate"], { env, stdio: "ignore" });
  execFileSync(bin("tt-admin"), ["seed", "demo"], { env, stdio: "ignore" });
}
